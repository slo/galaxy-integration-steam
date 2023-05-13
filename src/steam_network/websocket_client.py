import asyncio
from asyncio.futures import Future
import logging
import ssl
from contextlib import suppress
from typing import Callable, Optional, Any, Dict

import websockets
from galaxy.api.errors import BackendNotAvailable, BackendTimeout, BackendError, InvalidCredentials, NetworkError, AccessDenied

from rsa import PublicKey, encrypt

from steam_network.authentication_cache import AuthenticationCache

from .websocket_list import WebSocketList
from .friends_cache import FriendsCache
from .games_cache import GamesCache
from .local_machine_cache import LocalMachineCache
from .protocol_client import ProtocolClient
from .stats_cache import StatsCache
from .times_cache import TimesCache
from .user_info_cache import UserInfoCache

from .enums import AuthCall, TwoFactorMethod, UserActionRequired, to_helpful_string, to_UserAction

from .steam_public_key import SteamPublicKey
from .steam_auth_polling_data import SteamPollingData

from traceback import format_exc

logger = logging.getLogger(__name__)
# do not log low level events from websockets
logging.getLogger("websockets").setLevel(logging.WARNING)


RECONNECT_INTERVAL_SECONDS = 20
MAX_INCOMING_MESSAGE_SIZE = 2**24
BLACKLISTED_CM_EXPIRATION_SEC = 300


async def sleep(seconds: int):
    await asyncio.sleep(seconds)


def asyncio_future() -> Future:
    loop = asyncio.get_event_loop()
    return loop.create_future()


class WebSocketClient:
    def __init__(
        self,
        websocket_list: WebSocketList,
        ssl_context: ssl.SSLContext,
        friends_cache: FriendsCache,
        games_cache: GamesCache,
        translations_cache: dict,
        stats_cache: StatsCache,
        times_cache: TimesCache,
        authentication_cache: AuthenticationCache,
        user_info_cache: UserInfoCache,
        local_machine_cache: LocalMachineCache,
    ):
        self._ssl_context = ssl_context
        self._websocket: Optional[websockets.client.WebSocketClientProtocol] = None
        self._protocol_client: Optional[ProtocolClient] = None
        self._websocket_list = websocket_list

        self._friends_cache = friends_cache
        self._games_cache = games_cache
        self._translations_cache = translations_cache
        self._stats_cache = stats_cache
        self._authentication_cache = authentication_cache
        self._user_info_cache = user_info_cache
        self._local_machine_cache = local_machine_cache
        self._times_cache = times_cache

        self.communication_queues = {'plugin': asyncio.Queue(), 'websocket': asyncio.Queue(),}
        self.used_server_cell_id: int = 0
        self._current_ws_address: Optional[str] = None

        self._steam_public_key : Optional[SteamPublicKey] = None
        self._steam_polling_data : Optional[SteamPollingData] = None

    async def run(self, create_future_factory: Callable[[], Future]=asyncio_future):
        while True:
            try:
                await self._ensure_connected()

                run_task = asyncio.create_task(self._protocol_client.run())
                auth_lost = create_future_factory()
                auth_task = asyncio.create_task(self._all_auth_calls(auth_lost))
                pending = None
                try:
                    done, pending = await asyncio.wait({run_task, auth_task}, return_when=asyncio.FIRST_COMPLETED)
                    if auth_task in done:
                        await auth_task

                    done, pending = await asyncio.wait({run_task, auth_lost}, return_when=asyncio.FIRST_COMPLETED)
                    if auth_lost in done:
                        try:
                            await auth_lost
                        except (InvalidCredentials, AccessDenied) as e:
                            logger.warning(f"Auth lost by a reason: {repr(e)}")
                            await self._close_socket()
                            await self._close_protocol_client()
                            run_task.cancel()
                            break

                    assert run_task in done
                    await run_task
                    break
                except Exception:
                    with suppress(asyncio.CancelledError):
                        if pending is not None:
                            for task in pending:
                                task.cancel()
                                await task
                    raise
            except asyncio.CancelledError as e:
                logger.warning(f"Websocket task cancelled {repr(e)}")
                raise
            except websockets.exceptions.ConnectionClosedOK as e:
                logger.debug(format_exc())
                logger.debug("Expected WebSocket disconnection")
            except websockets.exceptions.ConnectionClosedError as error:
                logger.warning("WebSocket disconnected (%d: %s), reconnecting...", error.code, error.reason)
            except websockets.exceptions.InvalidState as error:
                logger.warning(f"WebSocket is trying to connect... {repr(error)}")
            except (BackendNotAvailable, BackendTimeout, BackendError) as error:
                logger.warning(f"{repr(error)}. Trying with different CM...")
                self._websocket_list.add_server_to_ignored(self._current_ws_address, timeout_sec=BLACKLISTED_CM_EXPIRATION_SEC)
            except NetworkError as error:
                logger.error(
                    f"Failed to establish authenticated WebSocket connection: {repr(error)}, retrying after %d seconds",
                    RECONNECT_INTERVAL_SECONDS
                )
                await sleep(RECONNECT_INTERVAL_SECONDS)
                continue
            except Exception as e:
                logger.error(f"Failed to establish authenticated WebSocket connection {repr(e)}")
                logger.error(format_exc())
                raise

            await self._close_socket()
            await self._close_protocol_client()

    async def _close_socket(self):
        if self._websocket is not None:
            logger.info("Closing websocket")
            await self._websocket.close()
            await self._websocket.wait_closed()
            self._websocket = None

    async def _close_protocol_client(self):
        is_socket_connected = True if self._websocket else False
        if self._protocol_client is not None:
            logger.info("Closing protocol client")
            await self._protocol_client.close(send_log_off=is_socket_connected)
            await self._protocol_client.wait_closed()
            self._protocol_client = None

    async def close(self):
        is_socket_connected = True if self._websocket else False
        if self._protocol_client is not None:
            await self._protocol_client.close(send_log_off=is_socket_connected)
        if self._websocket is not None:
            await self._websocket.close()

    async def wait_closed(self):
        if self._protocol_client is not None:
            await self._protocol_client.wait_closed()
        if self._websocket is not None:
            await self._websocket.wait_closed()

    async def get_friends(self):
        await self._friends_cache.wait_ready()
        return [str(user_id) for user_id in self._friends_cache.get_keys()]

    async def get_friends_nicknames(self):
        await self._friends_cache.wait_nicknames_ready()
        return self._friends_cache.get_nicknames()

    async def get_friends_info(self, users):
        await self._friends_cache.wait_ready()
        result = {}
        for user_id in users:
            int_user_id = int(user_id)
            user_info = self._friends_cache.get(int_user_id)
            if user_info is not None:
                result[user_id] = user_info
        return result

    async def refresh_game_stats(self, game_ids):
        self._stats_cache.start_game_stats_import(game_ids)
        await self._protocol_client.import_game_stats(game_ids)

    async def refresh_game_times(self):
        self._times_cache.start_game_times_import()
        await self._protocol_client.import_game_times()

    async def retrieve_collections(self):
        return await self._protocol_client.retrieve_collections()


    async def _ensure_connected(self):
        if self._protocol_client is not None:
            return # already connected

        while True:
            async for ws_address in self._websocket_list.get(self.used_server_cell_id):
                self._current_ws_address = ws_address
                try:
                    self._websocket = await asyncio.wait_for(websockets.client.connect(ws_address, ssl=self._ssl_context, max_size=MAX_INCOMING_MESSAGE_SIZE), 5)
                    self._protocol_client = ProtocolClient(self._websocket, self._friends_cache, self._games_cache, self._translations_cache, self._stats_cache, self._times_cache, self._authentication_cache, self._user_info_cache, self._local_machine_cache, self.used_server_cell_id)
                    logger.info(f'Connected to Steam on CM {ws_address} on cell_id {self.used_server_cell_id}. Sending Hello')
                    await self._protocol_client.finish_handshake()
                    return
                except (asyncio.TimeoutError, OSError, websockets.exceptions.InvalidURI, websockets.exceptions.InvalidHandshake):
                    self._websocket_list.add_server_to_ignored(self._current_ws_address, timeout_sec=BLACKLISTED_CM_EXPIRATION_SEC)
                    continue

            logger.exception(
                "Failed to connect to any server, reconnecting in %d seconds...",
                RECONNECT_INTERVAL_SECONDS
            )
            await sleep(RECONNECT_INTERVAL_SECONDS)

    async def _all_auth_calls(self, auth_lost_future):
        async def auth_lost_handler(error):
            logger.warning("WebSocket client authentication lost")
            auth_lost_future.set_exception(error)

        ret_code : Optional[UserActionRequired] = None
        while ret_code != UserActionRequired.NoActionRequired:
            if ret_code != None:
                await self.communication_queues['plugin'].put({'auth_result': ret_code})
                logger.info(f"Put {ret_code} in the queue, waiting for other side to receive")

            ret_code = None
            response : Dict[str,Any] = await self.communication_queues['websocket'].get()
            logger.info(f" Got {response.keys()} from queue")
            #change the way the code is done here. Since the workflow changed, on call on the protocol client isn't going to work anymore. we need to do a bunch of shit. 
            mode = response.get('mode', 'rsa');
            if (mode == AuthCall.RSA):
                logger.info(f'Retrieving RSA Public Key for {"username" if self._user_info_cache.account_username else ""}')
                (code, key) = await self._protocol_client.get_rsa_public_key(self._user_info_cache.account_username, auth_lost_handler)
                self._steam_public_key = key
                ret_code = code
            elif (mode == AuthCall.LOGIN):
                password :str = response.get('password', None)
                logger.info(f'Authenticating with {"username" if self._user_info_cache.account_username else ""}, {"password" if password else ""}')
                if (password):
                    enciphered = encrypt(password.encode('utf-8',errors="ignore"), self._steam_public_key.rsa_public_key)
                    self._steam_polling_data = await self._protocol_client.authenticate_password(self._user_info_cache.account_username, enciphered, self._steam_public_key.timestamp, auth_lost_handler)
                    if (self._steam_polling_data):
                        self._authentication_cache.two_factor_method = self._steam_polling_data.confirmation_method
                        self._authentication_cache.error_message = self._steam_polling_data.extended_error_message
                        self._authentication_cache.status_message = self._steam_polling_data.confirmation_message
                        ret_code = to_UserAction(self._authentication_cache.two_factor_method)
                    else:
                        ret_code = UserActionRequired.InvalidAuthData

                    if (ret_code != UserActionRequired.InvalidAuthData):
                        logger.info("GOT THE LOGIN DONE! ON TO 2FA")
                    else:
                        logger.info("LOGIN FAILED :( But hey, at least you're here!")
                else:
                    ret_code = UserActionRequired.InvalidAuthData
            elif (mode == AuthCall.UPDATE_TWO_FACTOR):
                code : Optional[UserActionRequired] = response.get('two-factor-code', None)
                if (self._steam_polling_data is None or self._steam_polling_data.confirmation_method == TwoFactorMethod.Unknown or not code):
                    ret_code = UserActionRequired.InvalidAuthData
                else:
                    logger.info(f'Updating two-factor with provided ' + to_helpful_string(self._authentication_cache.two_factor_method))
                    ret_code = await self._protocol_client.update_two_factor(self._steam_polling_data.client_id, self._steam_polling_data.steam_id, code, self._authentication_cache.two_factor_method, auth_lost_handler)
            elif (mode == AuthCall.POLL_TWO_FACTOR):
                logger.info("Polling to see if the user has completed any steam-guard related stuff")
                (ret_code, new_client_id) = await self._protocol_client.check_auth_status(self._steam_polling_data.client_id, self._steam_polling_data.request_id, auth_lost_handler)
                if (new_client_id is not None):
                    self._steam_polling_data.client_id = new_client_id
            elif (mode == AuthCall.RENEW):
                logger.info("God, i hope this works.")
                ret_code = await self._protocol_client.revoke_and_renew(auth_lost_handler)
            elif (mode == AuthCall.TOKEN):
                logger.info("Finalizing Log in using the new auth refresh token and the classic login call")
                ret_code = await self._protocol_client.finalize_login(self._user_info_cache.account_username, self._user_info_cache.steam_id, self._user_info_cache.refresh_token, auth_lost_handler)
            else:
                ret_code = UserActionRequired.InvalidAuthData

            logger.info(f"Response from auth {ret_code}")

        logger.info("Finished authentication")

        await self.communication_queues['plugin'].put({'auth_result': ret_code})
