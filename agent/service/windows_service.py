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

        def SvcDoRun(self):
            from agent.config import AGENT_VERSION
            from agent.ipc.protocol import ServiceProtocolHandler
            from agent.service.main import build_service_runtime

            servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")
            self._runtime = build_service_runtime()
            handler = ServiceProtocolHandler(
                self._runtime.execution_service.handle, AGENT_VERSION
            )
            self._runtime.start(handler)
            win32event.WaitForSingleObject(self._stop_handle, win32event.INFINITE)
            servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")

    win32serviceutil.HandleCommandLine(
        EECPWindowsService,
        argv=[sys.argv[0], command],
    )
