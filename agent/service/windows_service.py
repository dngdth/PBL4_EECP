from __future__ import annotations

import os
import sys
from typing import Any

SERVICE_NAME = "EECPAgentService"
SERVICE_DISPLAY_NAME = "EECP Privileged Agent Service"

# Values defined by the Windows WTS API. Console connect/disconnect are
# intentionally not treated as logon/logoff evidence.
WTS_CONSOLE_CONNECT = 1
WTS_CONSOLE_DISCONNECT = 2
WTS_REMOTE_CONNECT = 3
WTS_REMOTE_DISCONNECT = 4
WTS_SESSION_LOGON = 5
WTS_SESSION_LOGOFF = 6
WTS_SESSION_LOCK = 7
WTS_SESSION_UNLOCK = 8

SESSION_ACTIONS = {
    WTS_REMOTE_CONNECT: "REMOTE_CONNECT",
    WTS_REMOTE_DISCONNECT: "REMOTE_DISCONNECT",
    WTS_SESSION_LOGON: "LOGON",
    WTS_SESSION_LOGOFF: "LOGOFF",
    WTS_SESSION_LOCK: "LOCK",
    WTS_SESSION_UNLOCK: "UNLOCK",
}

try:
    if os.name != "nt":
        raise ImportError("not running on Windows")
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError as exc:  # Keep the module importable in non-Windows CI.
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None
    _PYWIN32_IMPORT_ERROR: ImportError | None = exc

    class _ServiceFrameworkBase:
        pass

else:
    _PYWIN32_IMPORT_ERROR = None
    _ServiceFrameworkBase = win32serviceutil.ServiceFramework


def parse_windows_session_id(data: object) -> int | None:
    """Extract pywin32's WTSSESSION_NOTIFICATION session id safely."""

    candidate: object
    if isinstance(data, bool):
        return None
    if isinstance(data, int):
        candidate = data
    elif isinstance(data, (tuple, list)) and len(data) == 1:
        candidate = data[0]
    elif isinstance(data, dict):
        candidate = data.get("SessionId", data.get("session_id"))
    else:
        return None
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        return None
    return candidate


def session_action(event_type: object) -> str | None:
    if isinstance(event_type, bool) or not isinstance(event_type, int):
        return None
    return SESSION_ACTIONS.get(event_type)


def _query_session_identity(session_id: int) -> tuple[str | None, str | None]:
    try:
        import win32ts

        username = win32ts.WTSQuerySessionInformation(
            None, session_id, win32ts.WTSUserName
        )
        domain = win32ts.WTSQuerySessionInformation(
            None, session_id, win32ts.WTSDomainName
        )
        return str(username).strip() or None, str(domain).strip() or None
    except Exception as exc:
        if servicemanager is not None:
            servicemanager.LogWarningMsg(
                f"{SERVICE_NAME} session identity unavailable: {exc}"
            )
        return None, None


class EECPWindowsService(_ServiceFrameworkBase):
    """Module-level pywin32 entrypoint that SCM can import after installation."""

    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = "Runs the local-only EECP privileged policy enforcement boundary."

    def __init__(self, args: Any):
        _require_pywin32()
        super().__init__(args)
        self._stop_handle = win32event.CreateEvent(None, 0, 0, None)
        self._runtime = None

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self._runtime is not None:
            self._runtime.stop()
        win32event.SetEvent(self._stop_handle)

    def GetAcceptedControls(self) -> int:
        return super().GetAcceptedControls() | win32service.SERVICE_ACCEPT_SESSIONCHANGE

    def SvcOtherEx(self, control: int, event_type: int, data: object) -> None:
        if control != win32service.SERVICE_CONTROL_SESSIONCHANGE or self._runtime is None:
            return
        action = session_action(event_type)
        windows_session_id = parse_windows_session_id(data)
        if action is None or windows_session_id is None:
            servicemanager.LogWarningMsg(
                f"{SERVICE_NAME} ignored invalid session notification "
                f"event_type={event_type!r} data_type={type(data).__name__}"
            )
            return
        username, domain = _query_session_identity(windows_session_id)
        try:
            self._runtime.record_session_change(
                action=action,
                windows_session_id=windows_session_id,
                username=username,
                domain=domain,
            )
        except Exception as exc:
            servicemanager.LogErrorMsg(
                f"{SERVICE_NAME} could not journal session change: {exc}"
            )

    def SvcDoRun(self) -> None:
        from agent.service.environment import load_service_environment

        load_service_environment()
        from agent.service.main import build_protocol_handler, build_service_runtime

        servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")
        self._runtime = build_service_runtime()
        try:
            import win32ts

            snapshot: dict[int, tuple[str, str | None]] = {}
            for item in win32ts.WTSEnumerateSessions(None, 1, 0):
                session_id = int(
                    item.get("SessionId") if isinstance(item, dict) else item[0]
                )
                username, domain = _query_session_identity(session_id)
                if username:
                    snapshot[session_id] = (username, domain)
            self._runtime.seed_session_snapshot(snapshot)
        except Exception as exc:
            servicemanager.LogWarningMsg(
                f"{SERVICE_NAME} session snapshot unavailable: {exc}"
            )
        handler = build_protocol_handler(self._runtime)
        self._runtime.start(handler)
        try:
            win32event.WaitForSingleObject(self._stop_handle, win32event.INFINITE)
        finally:
            self._runtime.stop()
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")


def _require_pywin32() -> None:
    if os.name != "nt":
        raise OSError("Windows Service Control Manager is available only on Windows")
    if _PYWIN32_IMPORT_ERROR is not None:
        raise OSError(
            "pywin32 is required for SCM commands; install it on the Windows target"
        ) from _PYWIN32_IMPORT_ERROR


def run_scm_command(command: str) -> None:
    _require_pywin32()
    win32serviceutil.HandleCommandLine(
        EECPWindowsService,
        argv=[sys.argv[0], command],
    )
