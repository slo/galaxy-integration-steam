import asyncio
import struct
import gzip
import json
import logging
from itertools import count
from typing import Awaitable, Callable,Dict, Optional, Any
from galaxy.api.errors import UnknownBackendResponse

from protocol.messages import steammessages_base_pb2, steammessages_clientserver_login_pb2, steammessages_player_pb2, \
    steammessages_clientserver_friends_pb2, steammessages_clientserver_pb2, steamui_libraryroot_pb2, steammessages_clientserver_2_pb2
from protocol.consts import EMsg, EResult, EAccountType, EFriendRelationship, EPersonaState
from protocol.types import SteamId, ProtoUserInfo

import vdf
import hashlib

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ProtobufClient:
    _PROTO_MASK = 0x80000000
    _ACCOUNT_ID_MASK = 0x0110000100000000

    def __init__(self, set_socket):
        self._socket = set_socket
        self.log_on_handler: Optional[Callable[[EResult], Awaitable[None]]] = None
        self.log_off_handler: Optional[Callable[[EResult], Awaitable[None]]] = None
        self.relationship_handler: Optional[Callable[[bool, Dict[int, EFriendRelationship]], Awaitable[None]]] = None
        self.user_info_handler: Optional[Callable[[int, ProtoUserInfo], Awaitable[None]]] = None
        self.user_nicknames_handler: Optional[Callable[[dict], Awaitable[None]]] = None
        self.license_import_handler: Optional[Callable[[int], Awaitable[None]]] = None
        self.app_info_handler: Optional[Callable[[int, str], Awaitable[None]]] = None
        self.package_info_handler: Optional[Callable[[int], Awaitable[None]]] = None
        self.translations_handler: Optional[Callable[[int, Any], Awaitable[None]]] = None
        self.stats_handler: Optional[Callable[[int, Any, Any], Awaitable[None]]] = None
        self.user_authentication_handler: Optional[Callable[[str, Any], Awaitable[None]]] = None
        self.sentry: Optional[Callable[[], Awaitable[None]]] = None
        self.steam_id: Optional[int] = None
        self.times_handler: Optional[Callable[[int, int, int], Awaitable[None]]] = None
        self.times_import_finished_handler: Optional[Callable[[bool], Awaitable[None]]] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._session_id: Optional[int] = None
        self._job_id_iterator = count(1)
        self.job_list = []

        self.account_info_retrieved = asyncio.Event()
        self.login_key_retrieved = asyncio.Event()
        self.collections = {'event': asyncio.Event(),
                            'collections': dict()}

    async def close(self):
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()

    async def wait_closed(self):
        pass

    async def _process_packets(self):
        pass

    async def run(self):
        while True:
            for job in self.job_list.copy():
                logger.info(f"New job on list {job}")
                if job['job_name'] == "import_game_stats":
                    await self._import_game_stats(job['game_id'])
                    self.job_list.remove(job)
                if job['job_name'] == "import_collections":
                    await self._import_collections()
                    self.job_list.remove(job)
                if job['job_name'] == "import_game_times":
                    await self._import_game_time()
                    self.job_list.remove(job)
            try:
                packet = await asyncio.wait_for(self._socket.recv(), 0.1)
                await self._process_packet(packet)
            except asyncio.TimeoutError:
                pass

    async def log_on_web_auth(self, steam_id, miniprofile_id, account_name, token):
        # magic numbers taken from JavaScript Steam client
        message = steammessages_clientserver_login_pb2.CMsgClientLogon()
        message.account_name = account_name
        message.protocol_version = 65580
        message.qos_level = 2
        message.client_os_type = 4294966596
        message.ui_mode = 4
        message.chat_mode = 2
        message.web_logon_nonce = token
        message.client_instance_id = 0

        try:
            self.steam_id = steam_id
            await self.user_authentication_handler('steam_id', self.steam_id)
            await self.user_authentication_handler('account_id', miniprofile_id)
            await self._send(EMsg.ClientLogon, message)
        except Exception:
            self.steam_id = None
            raise

    async def log_on_password(self, account_name, password, two_factor, two_factor_type):
        message = steammessages_clientserver_login_pb2.CMsgClientLogon()
        message.account_name = account_name
        message.protocol_version = 65579
        message.password = password
        message.should_remember_password = True
        message.supports_rate_limit_response = True

        if two_factor:
            if two_factor_type == 'email':
                message.auth_code = two_factor
            elif two_factor_type == 'mobile':
                message.two_factor_code = two_factor
        logger.info("Sending log on message using credentials")
        await self._send(EMsg.ClientLogon, message)

    async def log_on_token(self, steam_id, account_name, token):
        message = steammessages_clientserver_login_pb2.CMsgClientLogon()
        message.account_name = account_name
        message.protocol_version = 65579
        message.should_remember_password = True
        message.supports_rate_limit_response = True
        message.login_key = token

        sentry = await self.sentry()
        if sentry:
            logger.info("Sentry present")
            message.eresult_sentryfile = EResult.OK
            message.sha_sentryfile = sentry

        self.steam_id = steam_id
        logger.info("Sending log on message using token")
        await self._send(EMsg.ClientLogon, message)

    async def _import_game_stats(self, game_id):
        logger.info(f"Importing game stats for {game_id}")
        message = steammessages_clientserver_pb2.CMsgClientGetUserStats()
        message.game_id = int(game_id)
        await self._send(EMsg.ClientGetUserStats, message)

    async def _import_game_time(self):
        logger.info(f"Importing game times")
        job_id = next(self._job_id_iterator)
        message = steammessages_player_pb2.CPlayer_CustomGetLastPlayedTimes_Request()
        message.min_last_played = 0
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None, "Player.ClientGetLastPlayedTimes#1")

    async def set_persona_state(self, state):
        message = steammessages_clientserver_friends_pb2.CMsgClientChangeStatus()
        message.persona_state = state
        await self._send(EMsg.ClientChangeStatus, message)

    async def get_friends_statuses(self):
        job_id = next(self._job_id_iterator)
        message = steamui_libraryroot_pb2.CChat_RequestFriendPersonaStates_Request()
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None, "Chat.RequestFriendPersonaStates#1")

    async def get_user_infos(self, users, flags):
        message = steammessages_clientserver_friends_pb2.CMsgClientRequestFriendData()
        message.friends.extend(users)
        message.persona_state_requested = flags
        await self._send(EMsg.ClientRequestFriendData, message)

    async def _import_collections(self):
        job_id = next(self._job_id_iterator)
        message = steamui_libraryroot_pb2.CCloudConfigStore_Download_Request()
        message_inside = steamui_libraryroot_pb2.CCloudConfigStore_NamespaceVersion()
        message_inside.enamespace = 1
        message.versions.append(message_inside)
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None, "CloudConfigStore.Download#1")

    async def get_packages_info(self, package_ids):
        logger.info(f"Sending call {EMsg.PICSProductInfoRequest} with {package_ids}")
        message = steammessages_clientserver_pb2.CMsgClientPICSProductInfoRequest()

        for package_id in package_ids:
            info = message.packages.add()
            info.packageid = int(package_id)

        await self._send(EMsg.PICSProductInfoRequest, message)

    async def get_apps_info(self, app_ids):
        logger.info(f"Sending call {EMsg.PICSProductInfoRequest} with {app_ids}")
        message = steammessages_clientserver_pb2.CMsgClientPICSProductInfoRequest()

        for app_id in app_ids:
            info = message.apps.add()
            info.appid = app_id

        await self._send(EMsg.PICSProductInfoRequest, message)

    async def get_presence_localization(self, appid, language='english'):
        logger.info(f"Sending call for rich presence localization with {appid}, {language}")
        message = steamui_libraryroot_pb2.CCommunity_GetAppRichPresenceLocalization_Request()

        message.appid = appid
        message.language = language

        job_id = next(self._job_id_iterator)
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None,  target_job_name='Community.GetAppRichPresenceLocalization#1')

    async def accept_update_machine_auth(self,jobid_target, sha_hash, offset, filename, cubtowrite):
        logger.info("Accepting update machine auth")
        message = steammessages_clientserver_2_pb2.CMsgClientUpdateMachineAuthResponse()
        message.filename = filename
        message.eresult = EResult.OK
        message.sha_file = sha_hash
        message.getlasterror = 0
        message.offset = offset
        message.cubwrote = cubtowrite

        await self._send(EMsg.ClientUpdateMachineAuthResponse, message, None, jobid_target, None)

    async def _send(
            self,
            emsg,
            message,
            source_job_id=None,
            target_job_id=None,
            target_job_name=None
    ):
        proto_header = steammessages_base_pb2.CMsgProtoBufHeader()
        if self.steam_id is not None:
            proto_header.steamid = self.steam_id
        else:
            proto_header.steamid = 0 + self._ACCOUNT_ID_MASK
        if self._session_id is not None:
            proto_header.client_sessionid = self._session_id
        if source_job_id is not None:
            proto_header.jobid_source = source_job_id
        if target_job_id is not None:
            proto_header.jobid_target = target_job_id
        if target_job_name is not None:
            proto_header.target_job_name = target_job_name

        header = proto_header.SerializeToString()

        body = message.SerializeToString()
        data = struct.pack("<2I", emsg | self._PROTO_MASK, len(header))
        data = data + header + body

        logger.debug("Sending message %d (%d bytes)", emsg, len(data))
        await self._socket.send(data)

    async def _heartbeat(self, interval):
        message = steammessages_clientserver_login_pb2.CMsgClientHeartBeat()
        while True:
            await asyncio.sleep(interval)
            await self._send(EMsg.ClientHeartBeat, message)

    async def _process_packet(self, packet):
        package_size = len(packet)
        logger.debug("Processing packet of %d bytes", package_size)
        if package_size < 8:
            logger.warning("Package too small, ignoring...")
        raw_emsg = struct.unpack("<I", packet[:4])[0]
        emsg = raw_emsg & ~self._PROTO_MASK
        if raw_emsg & self._PROTO_MASK != 0:
            header_len = struct.unpack("<I", packet[4:8])[0]
            header = steammessages_base_pb2.CMsgProtoBufHeader()
            header.ParseFromString(packet[8:8 + header_len])
            if self._session_id is None and header.client_sessionid != 0:
                logger.info("Session id: %d", header.client_sessionid)
                self._session_id = header.client_sessionid
            await self._process_message(emsg, header, packet[8 + header_len:])
        else:
            logger.warning("Packet with extended header - ignoring")

    async def _process_message(self, emsg, header, body):
        logger.debug("Processing message %d", emsg)
        if emsg == EMsg.Multi:
            await self._process_multi(body)
        elif emsg == EMsg.ClientLogOnResponse:
            await self._process_client_log_on_response(body)
        elif emsg == EMsg.ClientLogOff:
            await self._process_client_log_off(body)
        elif emsg == EMsg.ClientFriendsList:
            await self._process_client_friend_list(body)
        elif emsg == EMsg.ClientPersonaState:
            await self._process_client_persona_state(body)
        elif emsg == EMsg.ClientLicenseList:
            await self._process_license_list(body)
        elif emsg == EMsg.PICSProductInfoResponse:
            await self._process_package_info_response(body)
        elif emsg == EMsg.ClientGetUserStatsResponse:
            await self._process_user_stats_response(body)
        elif emsg == EMsg.ClientAccountInfo:
            await self._process_account_info(body)
        elif emsg == EMsg.ClientNewLoginKey:
            await self._process_client_new_login_key(body)
        elif emsg == EMsg.ClientUpdateMachineAuth:
            await self._process_client_update_machine_auth(body, header.jobid_source)
        elif emsg == EMsg.ClientPlayerNicknameList:
            await self._process_user_nicknames(body)
        elif emsg == EMsg.ServiceMethod:
            await self._process_service_method_response(header.target_job_name, header.jobid_target, body)
        elif emsg == EMsg.ServiceMethodResponse:
            await self._process_service_method_response(header.target_job_name, header.jobid_target, body)
        else:
            logger.warning("Ignored message %d", emsg)

    async def _process_multi(self, body):
        logger.debug("Processing message Multi")
        message = steammessages_base_pb2.CMsgMulti()
        message.ParseFromString(body)
        if message.size_unzipped > 0:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, gzip.decompress, message.message_body)
        else:
            data = message.message_body

        data_size = len(data)
        offset = 0
        size_bytes = 4
        while offset + size_bytes <= data_size:
            size = struct.unpack("<I", data[offset:offset + size_bytes])[0]
            await self._process_packet(data[offset + size_bytes:offset + size_bytes + size])
            offset += size_bytes + size
        logger.debug("Finished processing message Multi")

    async def _process_client_log_on_response(self, body):
        logger.debug("Processing message ClientLogOnResponse")
        message = steammessages_clientserver_login_pb2.CMsgClientLogonResponse()
        message.ParseFromString(body)
        result = message.eresult

        if result == EResult.AccountLogonDenied:
            if message.email_domain:
                await self.user_authentication_handler('two_step', 'email')
        if result == EResult.AccountLoginDeniedNeedTwoFactor:
            await self.user_authentication_handler('two_step', 'mobile')

        if result == EResult.OK:
            interval = message.out_of_game_heartbeat_seconds
            self.steam_id = message.client_supplied_steamid
            await self.user_authentication_handler('steam_id', self.steam_id)
            await self.user_authentication_handler('account_id', message.client_supplied_steamid - self._ACCOUNT_ID_MASK)
            self._heartbeat_task = asyncio.create_task(self._heartbeat(interval))

        if self.log_on_handler is not None:
            await self.log_on_handler(result)

    async def _process_client_update_machine_auth(self, body, jobid_source):
        logger.debug("Processing message ClientUpdateMachineAuth")
        message = steammessages_clientserver_2_pb2.CMsgClientUpdateMachineAuth()
        message.ParseFromString(body)

        sentry_sha = hashlib.sha1(message.bytes).digest()
        await self.user_authentication_handler('sentry', sentry_sha)
        await self.accept_update_machine_auth(jobid_source, sentry_sha, message.offset, message.filename, message.cubtowrite)

    async def _process_account_info(self, body):
        logger.debug("Processing message ClientAccountInfo")
        message = steammessages_clientserver_login_pb2.CMsgClientAccountInfo()
        message.ParseFromString(body)
        await self.user_authentication_handler('persona_name', message.persona_name)
        self.account_info_retrieved.set()

    async def _process_client_new_login_key(self, body):
        logger.debug("Processing message ClientNewLoginKey")
        message = steammessages_clientserver_login_pb2.CMsgClientNewLoginKey()
        message.ParseFromString(body)
        await self.user_authentication_handler('token', message.login_key)
        self.login_key_retrieved.set()

    async def _process_client_log_off(self, body):
        logger.debug("Processing message ClientLoggedOff")
        message = steammessages_clientserver_login_pb2.CMsgClientLoggedOff()
        message.ParseFromString(body)
        result = message.eresult

        assert self._heartbeat_task is not None
        self._heartbeat_task.cancel()

        if self.log_off_handler is not None:
            await self.log_off_handler(result)

    async def _process_user_nicknames(self, body):
        logger.debug("Processing message ClientPlayerNicknameList")
        message = steammessages_clientserver_friends_pb2.CMsgClientPlayerNicknameList()
        message.ParseFromString(body)
        nicknames = {}
        for player_nickname in message.nicknames:
            nicknames[str(player_nickname.steamid)] = player_nickname.nickname

        await self.user_nicknames_handler(nicknames)

    async def _process_client_friend_list(self, body):
        logger.debug("Processing message ClientFriendsList")
        if self.relationship_handler is None:
            return

        message = steammessages_clientserver_friends_pb2.CMsgClientFriendsList()
        message.ParseFromString(body)
        friends = {}
        for relationship in message.friends:
            steam_id = relationship.ulfriendid
            details = SteamId.parse(steam_id)
            if details.type_ == EAccountType.Individual:
                friends[steam_id] = EFriendRelationship(relationship.efriendrelationship)

        await self.relationship_handler(message.bincremental, friends)

    async def _process_client_persona_state(self, body):
        logger.debug("Processing message ClientPersonaState")
        if self.user_info_handler is None:
            return

        message = steammessages_clientserver_friends_pb2.CMsgClientPersonaState()
        message.ParseFromString(body)

        for user in message.friends:
            user_id = user.friendid
            if user_id == self.steam_id and int(user.game_played_app_id) != 0:
                await self.get_apps_info([int(user.game_played_app_id)])
            user_info = ProtoUserInfo()
            if user.HasField("player_name"):
                user_info.name = user.player_name
            if user.HasField("avatar_hash"):
                user_info.avatar_hash = user.avatar_hash
            if user.HasField("persona_state"):
                user_info.state = EPersonaState(user.persona_state)
            if user.HasField("gameid"):
                user_info.game_id = user.gameid
                rich_presence: Dict[str, str] = {}
                for element in user.rich_presence:
                    rich_presence[element.key] = element.value
                    if element.key == 'status' and element.value:
                        if element.value[0] == "#":
                            await self.translations_handler(user.gameid)
                    if element.key == 'steam_display' and element.value:
                        if element.value[0] == "#":
                            await self.translations_handler(user.gameid)
                user_info.rich_presence = rich_presence
            if user.HasField("game_name"):
                user_info.game_name = user.game_name

            await self.user_info_handler(user_id, user_info)

    async def _process_license_list(self, body):
        logger.debug("Processing message ClientLicenseList")
        if self.license_import_handler is None:
            return

        message = steammessages_clientserver_pb2.CMsgClientLicenseList()
        message.ParseFromString(body)

        licenses_to_check = []
        for license in message.licenses:
            # license.type 1024 = free games
            # license.flags 520 = unidentified trash entries (games which are not owned nor are free)
            if int(license.owner_id) == int(self.steam_id - self._ACCOUNT_ID_MASK) and int(license.flags) != 520:
                licenses_to_check.append(license)

        await self.license_import_handler(licenses_to_check)

    async def _process_package_info_response(self, body):
        logger.debug("Processing message PICSProductInfoResponse")
        message = steammessages_clientserver_pb2.CMsgClientPICSProductInfoResponse()
        message.ParseFromString(body)
        apps_to_parse = []

        for info in message.packages:
            await self.package_info_handler(str(info.packageid))
            if info.packageid == 0:
                # Packageid 0 contains trash entries for every user
                logger.info("Skipping packageid 0 ")
                continue
            package_content = vdf.binary_loads(info.buffer[4:])
            for app in package_content[str(info.packageid)]['appids']:
                await self.app_info_handler(mother_appid=str(info.packageid), appid=str(package_content[str(info.packageid)]['appids'][app]))
                apps_to_parse.append(package_content[str(info.packageid)]['appids'][app])

        for info in message.apps:
            app_content = vdf.loads(info.buffer[:-1].decode('utf-8', 'replace'))
            try:
                if app_content['appinfo']['common']['type'].lower() == 'game':
                    await self.app_info_handler(appid=str(app_content['appinfo']['appid']), title=app_content['appinfo']['common']['name'], game=True)
                else:
                    await self.app_info_handler(appid=str(app_content['appinfo']['appid']), game=False)
            except KeyError:
                # Unrecognized app type
                await self.app_info_handler(appid=str(app_content['appinfo']['appid']), game=False)

        if len(apps_to_parse) > 0:
            logger.info(f"Apps to parse {apps_to_parse}, {len(apps_to_parse)} entries")
            await self.get_apps_info(apps_to_parse)

    async def _process_rich_presence_translations(self, body):
        message = steamui_libraryroot_pb2.CCommunity_GetAppRichPresenceLocalization_Response()
        message.ParseFromString(body)

        logger.info(f"Received information about rich presence translations for {message.appid}")
        await self.translations_handler(message.appid, message.token_lists)

    async def _process_user_stats_response(self, body):
        logger.debug("Processing message ClientGetUserStatsResponse")
        message = steammessages_clientserver_pb2.CMsgClientGetUserStatsResponse()
        message.ParseFromString(body)
        game_id = message.game_id
        stats = message.stats
        achievs = message.achievement_blocks

        logger.info(f"Processing user stats response for {message.game_id}")
        achievements_schema = vdf.binary_loads(message.schema,merge_duplicate_keys=False)
        achievements_unlocked = []

        for achievement_block in achievs:
            achi_block_enum = 32 * (achievement_block.achievement_id - 1)
            for index, unlock_time in enumerate(achievement_block.unlock_time):
                if unlock_time > 0:
                    if str(index) not in achievements_schema[str(game_id)]['stats'][str(achievement_block.achievement_id)]['bits']:
                        logger.info(f"Non existent achievement unlocked")
                        continue
                    try:
                        if 'english' in achievements_schema[str(game_id)]['stats'][str(achievement_block.achievement_id)]['bits'][str(index)]['display']['name']:
                            name = achievements_schema[str(game_id)]['stats'][str(achievement_block.achievement_id)]['bits'][str(index)]['display']['name']['english']
                        else:
                            name = achievements_schema[str(game_id)]['stats'][str(achievement_block.achievement_id)]['bits'][str(index)]['display']['name']
                        achievements_unlocked.append({'id': achi_block_enum+index,
                                                      'unlock_time': unlock_time,
                                                     'name': name})
                    except:
                        logger.info(f"Unable to parse achievement {index} from block {achievement_block.achievement_id}")
                        logger.info(achievs)
                        logger.info(achievements_schema)
                        logger.info(message.schema)
                        raise UnknownBackendResponse()

        await self.stats_handler(game_id, stats, achievements_unlocked)

    async def _process_user_time_response(self, body):
        logger.debug(f"Received information about game times")
        message = steammessages_player_pb2.CPlayer_CustomGetLastPlayedTimes_Response()
        message.ParseFromString(body)
        for game in message.games:
            logger.info(f"Processing game times for game {game.appid}, playtime: {game.playtime_forever} last time played: {game.last_playtime}")
            await self.times_handler(game.appid, game.playtime_forever, game.last_playtime)
        await self.times_import_finished_handler(True)

    async def _process_collections_response(self, body):
        message = steamui_libraryroot_pb2.CCloudConfigStore_Download_Response()
        message.ParseFromString(body)

        for data in message.data:
            for entry in data.entries:
                try:
                    loaded_val = json.loads(entry.value)
                    self.collections['collections'][loaded_val['name']] = loaded_val['added']
                except:
                    pass
        self.collections['event'].set()

    async def _process_service_method_response(self, target_job_name, target_job_id, body):
        logger.debug("Processing message ServiceMethodResponse %s", target_job_name)
        if target_job_name == 'Community.GetAppRichPresenceLocalization#1':
            await self._process_rich_presence_translations(body)
        if target_job_name == 'Player.ClientGetLastPlayedTimes#1':
            await self._process_user_time_response(body)
        if target_job_name == 'CloudConfigStore.Download#1':
            await self._process_collections_response(body)



