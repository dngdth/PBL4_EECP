from __future__ import annotations

import os
import sys

SERVICE_NAME = "EECPAgentService"
SERVICE_DISPLAY_NAME = "EECP Privileged Agent Service"


def run_scm_command(command: str) -> None:
    if os.name != "nt":
        raise OSError("Windows Service Control Manager is available only on Windows")
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil
    except ImportError as exc:
        raise OSError(
            "pywin32 is required for SCM commands; install it on the Windows target"
        ) from exc

    class EECPWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = (
            "Runs the local-only EECP privileged policy enforcement boundary."
        )

        def __init__(self, args):
            super().__init__(args)
            self._stop_handle = win32event.CreateEvent(None, 0, 0, None)
            self._runtime = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._runtime is not None:
                self._runtime.stop()
            win32event.SetEvent(self._stop_handle)

        def GetAcceptedControls(self):
            return super().GetAcceptedControls() | win32service.SERVICE_ACCEPT_SESSIONCHANGE

        def SvcOtherEx(self, control, event_type, data):
            if (
                control != win32service.SERVICE_CONTROL_SESSIONCHANGE
                or self._runtime is None
            ):
                return
            actions = {
                1: "LOGON",
                2: "LOGOFF",
                3: "REMOTE_CONNECT",
                4: "REMOTE_DISCONNECT",
                7: "LOCK",
                8: "UNLOCK",
            }
            action = actions.get(event_type)
            if action is None:
                return
            windows_session_id = int(data)
            username = None
            domain = None
            try:
                import win32ts

                username = win32ts.WTSQuerySessionInformation(
                    None, windows_session_id, win32ts.WTSUserName
                )
                domain = win32ts.WTSQuerySessionInformation(
                    None, windows_session_id, win32ts.WTSDomainName
                )
            except Exception as exc:
                servicemanager.LogWarningMsg(
                    f"{SERVICE_NAME} session identity unavailable: {exc}"
                )
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

        def SvcDoRun(self):
            from agent.service.main import build_protocol_handler, build_service_runtime

            servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")
            self._runtime = build_service_runtime()
            try:
                import win32ts

                snapshot = {}
                for item in win32ts.WTSEnumerateSessions(None, 1, 0):
                    session_id = int(
                        item.get("SessionId") if isinstance(item, dict) else item[0]
                    )
                    username = win32ts.WTSQuerySessionInformation(
                        None, session_id, win32ts.WTSUserName
                    )
                    if username:
                        snapshot[session_id] = username
                self._runtime.seed_session_snapshot(snapshot)
            except Exception as exc:
                servicemanager.LogWarningMsg(
                    f"{SERVICE_NAME} session snapshot unavailable: {exc}"
                )
            handler = build_protocol_handler(self._runtime)
            self._runtime.start(handler)
            win32event.WaitForSingleObject(self._stop_handle, win32event.INFINITE)
            servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")

    win32serviceutil.HandleCommandLine(
        EECPWindowsService,
        argv=[sys.argv[0], command],
    )
