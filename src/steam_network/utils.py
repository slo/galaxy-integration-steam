""" A collection of Top-Level Functions and other utilities that can be imported from one location so we don't need to hunt for them in this maze of a program.



"""
import platform

from galaxy.api.errors import (AccessDenied, BackendError, BackendNotAvailable,
                               BackendTimeout, Banned, InvalidCredentials,
                               NetworkError, TemporaryBlocked, UnknownError)
from galaxy.api.types import NextStep

from .enums import DisplayUriHelper
from .protocol.consts import EOSType, EResult


def get_os() -> EOSType:
    system = platform.system()
    if system == 'Windows':
        release = platform.release()
        releases = {
            'XP': EOSType.WinXP,
            'Vista': EOSType.WinVista,
            '7': EOSType.Windows7,
            '8': EOSType.Windows8,
            '8.1': EOSType.Windows81,
            '10': EOSType.Windows10,
            '10': EOSType.Windows10,
            '11': EOSType.Win11,
        }
        return releases.get(release, EOSType.WinUnknown)
    elif system == 'Darwin':
        release = platform.mac_ver()[0]
        releases = {
            '10.4': EOSType.MacOS104,
            '10.5': EOSType.MacOS105,
            '10.6': EOSType.MacOS106,
            '10.7': EOSType.MacOS107,
            '10.8': EOSType.MacOS108,
            '10.9': EOSType.MacOS109,
            '10.10': EOSType.MacOS1010,
            '10.11': EOSType.MacOS1011,
            '10.12': EOSType.MacOS1012,
            '10.13': EOSType.MacOS1013,
            '10.14': EOSType.MacOS1014,
            '10.15': EOSType.MacOS1015,
            '10.16': EOSType.MacOS1016,
            '11.0': EOSType.MacOS11,
            '11.1': EOSType.MacOS111,
            '10.17': EOSType.MacOS1017,
            '12.0': EOSType.MacOS12,
            '13.0': EOSType.MacOS13,
        }
        return releases.get(release, EOSType.MacOSUnknown)
    return EOSType.Unknown


def translate_error(result: EResult):
    assert result != EResult.OK
    data = {
        "result": result
    }
    if result in (
        EResult.InvalidPassword,
        EResult.AccountNotFound,
        EResult.InvalidSteamID,
        EResult.InvalidLoginAuthCode,
        EResult.AccountLogonDeniedNoMailSent,
        EResult.AccountLoginDeniedNeedTwoFactor,
        EResult.TwoFactorCodeMismatch,
        EResult.TwoFactorActivationCodeMismatch
    ):
        return InvalidCredentials(data)
    if result in (
        EResult.ConnectFailed,
        EResult.IOFailure,
        EResult.RemoteDisconnect
    ):
        return NetworkError(data)
    if result in (
        EResult.Busy,
        EResult.ServiceUnavailable,
        EResult.Pending,
        EResult.IPNotFound,
        EResult.TryAnotherCM,
        EResult.Cancelled
    ):
        return BackendNotAvailable(data)
    if result == EResult.Timeout:
        return BackendTimeout(data)
    if result in (
        EResult.RateLimitExceeded,
        EResult.LimitExceeded,
        EResult.Suspended,
        EResult.AccountLocked,
        EResult.AccountLogonDeniedVerifiedEmailRequired
    ):
        return TemporaryBlocked(data)
    if result == EResult.Banned:
        return Banned(data)
    if result in (
        EResult.AccessDenied,
        EResult.InsufficientPrivilege,
        EResult.LogonSessionReplaced,
        EResult.Blocked,
        EResult.Ignored,
        EResult.AccountDisabled,
        EResult.AccountNotFeatured
    ):
        return AccessDenied(data)
    if result in (
        EResult.DataCorruption,
        EResult.DiskFull,
        EResult.RemoteCallFailed,
        EResult.RemoteFileConflict,
        EResult.BadResponse
    ):
        return BackendError(data)

    return UnknownError(data)

_NEXT_STEP = {
    "window_title": "Login to Steam",
    "window_width": 500,
    "window_height": 460,
    "start_uri": None,
    "end_uri_regex": None
}

def next_step_response_simple(display: DisplayUriHelper, username: str, errored:bool = False, **kwargs) -> NextStep:
    next_step = _NEXT_STEP
    next_step['start_uri'] = display.GetStartUri(username, errored, **kwargs)
    next_step['end_uri_regex'] = display.GetEndUriRegex()

    return NextStep("web_session", next_step)
