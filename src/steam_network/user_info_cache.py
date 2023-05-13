import asyncio
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class UserInfoCache:
    def __init__(self):
        self._steam_id: Optional[int] = None #unique id Steam assigns to the user
        self._account_username: Optional[str] = None #user name for the steam account.
        self._persona_name: Optional[str] = None #friendly name the user goes by in their display. It's what we use when saying "logged in" in the integration page.
        #Note: The tokens below are strings, but they are formatted as JSON Web Tokens (JWT). We can parse them to determine when the refresh token will expire.
        self._refresh_token : Optional[str] = None #persistent token. Used to log in, despite the fact that we should use an access token. weird quirk in how steam does things.
        self._access_token : Optional[str] = None #session login token. Largely useless. May be useful in future if steam fixes their login to use an access token instead of refresh token. 

        self._guard_data : Optional[str] = None #steam guard data. It might no longer be necessary, but i'll save it just in case. 
        self._login_id : Optional[int] = None


        self._changed = False
        
        self.initialized = asyncio.Event()

    def _check_initialized(self):
        if self._steam_id and self._account_username and self._persona_name and self._refresh_token and self._guard_data:
            logger.info("User info cache initialized")
            self.initialized.set()
            self._changed = True

    def is_initialized(self) -> bool:
        #THIS CURRENTLY ENABLES OR DISABLES LOGGING IN FROM CACHE. 

        #type hinting didn't want to place nice if i didn't do it this way. if you can python better than me and get this to properly bool type hint, go for it -BaumherA

        #TO DISABLE, COMMENT THE FIRST AND UNCOMMENT THE SECOND. TO ENABLE, COMMENT THE SECOND, UNCOMMENT THE FIRST. 
        return True if (self._steam_id and self._account_username and self._persona_name and self._refresh_token and self._guard_data) else False
        #return False


    def to_dict(self):
        creds = {
            'steam_id': base64.b64encode(str(self._steam_id).encode('utf-8')).decode('utf-8'),
            'refresh_token': base64.b64encode(str(self._refresh_token).encode('utf-8')).decode('utf-8'),
            'access_token': base64.b64encode(str(self._access_token).encode('utf-8')).decode('utf-8'),
            'account_username': base64.b64encode(str(self._account_username).encode('utf-8')).decode('utf-8'),
            'persona_name': base64.b64encode(str(self._persona_name).encode('utf-8')).decode('utf-8'),
            'guard_data': base64.b64encode(str(self._guard_data).encode('utf-8')).decode('utf-8'),
        }
        return creds

    def from_dict(self, lookup):
        for key in lookup.keys():
            if lookup[key]:
                logger.info(f"Loaded {key} from stored credentials")

        if 'steam_id' in lookup:
            self._steam_id = int(base64.b64decode(lookup['steam_id']).decode('utf-8'))

        if 'account_username' in lookup:
            self._account_username = base64.b64decode(lookup['account_username']).decode('utf-8')

        if 'persona_name' in lookup:
            self._persona_name = base64.b64decode(lookup['persona_name']).decode('utf-8')

        if 'refresh_token' in lookup:
            self._refresh_token = base64.b64decode(lookup['refresh_token']).decode('utf-8')

        if 'access_token' in lookup:
            self._access_token = base64.b64decode(lookup['access_token']).decode('utf-8')

        if 'guard_data' in lookup:
            self._guard_data = base64.b64decode(lookup['guard_data']).decode('utf-8')

    @property
    def changed(self):
        if self._changed:
            self._changed = False
            return True
        return False

    @property
    def steam_id(self):
        return self._steam_id

    @steam_id.setter
    def steam_id(self, val):
        if self._steam_id != val and self.initialized.is_set():
            self._changed = True
        self._steam_id = val
        if not self.initialized.is_set():
            self._check_initialized()

    @property
    def account_username(self):
        return self._account_username

    @account_username.setter
    def account_username(self, val):
        if self._account_username != val and self.initialized.is_set():
            self._changed = True
        self._account_username = val
        if not self.initialized.is_set():
            self._check_initialized()

    @property
    def persona_name(self):
        return self._persona_name

    @persona_name.setter
    def persona_name(self, val):
        if self._persona_name != val and self.initialized.is_set():
            self._changed = True
        self._persona_name = val
        if not self.initialized.is_set():
            self._check_initialized()

    @property
    def access_token(self):
        return self._access_token

    @access_token.setter
    def access_token(self, val):
        if self._access_token != val and self.initialized.is_set():
            self._changed = True
        self._access_token = val
        if not self.initialized.is_set():
            self._check_initialized()

    @property
    def refresh_token(self):
        return self._refresh_token

    @refresh_token.setter
    def refresh_token(self, val):
        if self._refresh_token != val and self.initialized.is_set():
            self._changed = True
        self._refresh_token = val
        if not self.initialized.is_set():
            self._check_initialized()

    @property
    def guard_data(self):
        return self._guard_data

    @guard_data.setter
    def guard_data(self, val):
        if self._guard_data != val and self.initialized.is_set():
            self._changed = True
        self._guard_data = val
        if not self.initialized.is_set():
            self._check_initialized()
