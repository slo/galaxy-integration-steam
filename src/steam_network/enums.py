from typing import Optional, Dict, Tuple, Union
import enum

import yarl
import urllib

import os
import pathlib

import logging

from .protocol.messages.steammessages_auth_pb2 import CAuthentication_AllowedConfirmation, EAuthSessionGuardType 
from pprint import pformat

from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper

#a constant. this is the path to the current directory, as a uri. this typically means adding file:/// to the beginning
DIRNAME = yarl.URL(pathlib.Path(os.path.dirname(os.path.realpath(__file__))).as_uri())
#another constant. the path to "index.html" relative to the current directory.
WEBPAGE_RELATIVE_PATH = r'/custom_login/index.html'

logger = logging.getLogger(__name__)

#defines the modes we will send to the 'websocket' queue
class AuthCall:

    RSA =               'rsa'
    LOGIN =             'login'
    UPDATE_TWO_FACTOR = 'two-factor-update'
    POLL_TWO_FACTOR =   'poll-two-factor'
    RENEW =             'renew'
    TOKEN =             'token'

class DisplayUriHelper(enum.Enum):
    GET_USER = 0
    LOGIN = 1
    TWO_FACTOR_MAIL = 2
    TWO_FACTOR_MOBILE = 3
    TWO_FACTOR_CONFIRM = 4

    def _add_view(self, args:Dict[str,str]) -> Dict[str, str] :
        if (self == self.LOGIN):
            args["view"] = "login"
        elif (self == self.TWO_FACTOR_MAIL):
            args["view"] = "steamguard"
        elif (self == self.TWO_FACTOR_MOBILE):
            args["view"] = "steamauthenticator"
        elif (self == self.TWO_FACTOR_CONFIRM):
            args["view"] = "steamauthenticator_confirm"
        else:
            args["view"] = "user"
        return args

    def _get_errored(self, args: Dict[str,str], errored: bool, verbose: bool = False) -> Dict[str, str]:
        if (errored):
            args["errored"] = "true"
        elif (verbose):
            args["errored"] = "false"
        return args

    def GetStartUri(self, username: Optional[str] = None, errored : bool = False, **kwargs:str) -> str:
        #imho this is the most intuitive way of getting the start url. it's not the most "Pythonic" of means, but it is infinitely more readable.

        #url params go into a dict. urllib.urlencode will autmatically convert a dict to a string of properly concatenated url params ('&'). 
        #it also escapes any characters that HTTP does not like, which is why we aren't doing it manually. 
        args : Dict[str, str] = {}
        #the path to index.html, with a question and a placeholder for all the url parameters. 
        result : str = str(DIRNAME) + WEBPAGE_RELATIVE_PATH + "?"
        args = self._add_view(args)
        args = self._get_errored(args, errored, False)
        if (self == self.LOGIN):
            if (username is None):
                raise ValueError("username cannot be null in login view")
            else:
                args["username"] = username

        for key, value in kwargs:
            if (key not in args):
                args[key] = value

        #now, convert the dict of url params to a string. replace the placeholder with this string. return the result. 
        return result + urllib.parse.urlencode(args)
    
    def EndUri(self) -> str:
         if (self == self.LOGIN):
             return 'login_finished'
         elif (self == self.TWO_FACTOR_MAIL):
             return 'two_factor_mail_finished'
         elif (self == self.TWO_FACTOR_MOBILE):
             return 'two_factor_mobile_finished'
         elif (self == self.TWO_FACTOR_CONFIRM):
             return 'two_factor_confirm_finished'
         else:
             return 'user_finished'

    def GetEndUriRegex(self):
        return ".*" + self.EndUri() + ".*";

class UserActionRequired(enum.IntEnum):
    NoActionRequired = 0
    NoActionConfirmToken = 1
    NoActionConfirmLogin = 2 #No action required, but we still need to confirm login. New auth workflow requires we poll to get the login info. 
    TwoFactorRequired = 3 #any form of 2FA required. when set, check the related TwoFactorMethod enum for the thing we need to do. 
    PasswordRequired = 4
    TwoFactorExpired = 5
    InvalidAuthData = 6

#We're going to store this in the User Info Cache so we don't need to pass it everywhere. 
#WARNING! BE VERY CAREFUL WITH THIS: IT APPEARS IN THE USER INFO CACHE! IF IT IS EVER SAVED (toDict method), THIS ENUM BECOMES SOFT IMMUTABLE 
#(You can add members but cannot delete, and must parse all options for backwards-compatibility). So, don't, lol.
class TwoFactorMethod(enum.IntEnum):
    Nothing = 0
    PhoneCode = 1
    EmailCode = 2
    PhoneConfirm = 3
    Unknown = 4 
    #EmailConfirm = 5 #Does not exist? Likely something Steam thought about implementing and decided not to. if that changes, we can support it. 
    

def to_TwoFactorMethod(auth_enum : EnumTypeWrapper) -> TwoFactorMethod:
    if (isinstance(auth_enum, CAuthentication_AllowedConfirmation)):
        auth_enum = auth_enum.confirmation_type
    ret_val, _ = _to_TwoFactorMethod(auth_enum, None)
    return ret_val
    
def _to_TwoFactorMethod(auth_enum : EnumTypeWrapper, msg: Optional[str]) -> Tuple[TwoFactorMethod, str]:
    if (auth_enum == EAuthSessionGuardType.k_EAuthSessionGuardType_None):
        return (TwoFactorMethod.Nothing, msg)
    elif (auth_enum == EAuthSessionGuardType.k_EAuthSessionGuardType_EmailCode):
        return (TwoFactorMethod.EmailCode, msg)
    elif (auth_enum == EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceCode):
        return (TwoFactorMethod.PhoneCode, msg)
    elif (auth_enum == EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceConfirmation):
        return (TwoFactorMethod.PhoneConfirm, msg)
    else: #if (k_EAuthSessionGuardType_Unknown, k_EAuthSessionGuardType_LegacyMachineAuth, k_EAuthSessionGuardType_MachineToken, k_EAuthSessionGuardType_EmailConfirmation, or an invalid number
        return (TwoFactorMethod.Unknown, msg)

def to_TwoFactorWithMessage(allowed_confirmation : CAuthentication_AllowedConfirmation) -> Tuple[TwoFactorMethod, str]:
    return _to_TwoFactorMethod(allowed_confirmation.confirmation_type, allowed_confirmation.associated_message)

def to_EAuthSessionGuardType(actionRequired : TwoFactorMethod) -> EAuthSessionGuardType:
    if (actionRequired == TwoFactorMethod.Nothing):
        return EAuthSessionGuardType.k_EAuthSessionGuardType_None
    elif (actionRequired == TwoFactorMethod.EmailCode):
        return EAuthSessionGuardType.k_EAuthSessionGuardType_EmailCode
    elif (actionRequired == TwoFactorMethod.PhoneCode):
        return EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceCode
    elif (actionRequired == TwoFactorMethod.PhoneConfirm):
        return EAuthSessionGuardType.k_EAuthSessionGuardType_DeviceConfirmation
    else: #if TwoFactorMethod.InvalidAuthData or an invalid number
        return EAuthSessionGuardType.k_EAuthSessionGuardType_Unknown

def to_helpful_string(method:TwoFactorMethod) -> str:
    if (method == TwoFactorMethod.Nothing):
        return "<no two factor method>"
    elif (method == TwoFactorMethod.EmailCode):
        return "email code"
    elif (method == TwoFactorMethod.PhoneCode):
        return "phone code"
    elif (method == TwoFactorMethod.PhoneConfirm):
        return "phone confirmation"
    else: #if TwoFactorMethod.InvalidAuthData or an invalid number
        return "<unknown>"

def to_UserAction(method: TwoFactorMethod) -> UserActionRequired:
    if (method == TwoFactorMethod.Nothing):
        return UserActionRequired.NoActionConfirmLogin
    elif (method != TwoFactorMethod.Unknown):
        return UserActionRequired.TwoFactorRequired
    else: #if TwoFactorMethod.InvalidAuthData or an invalid number
        return UserActionRequired.InvalidAuthData