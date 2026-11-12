from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from contextlib import suppress

from agent.ipc._windows import (
    ERROR_OPERATION_ABORTED,
    ERROR_PIPE_CONNECTED,
    GENERIC_READ,
    GENERIC_WRITE,
    INVALID_HANDLE_VALUE,
    OPEN_EXISTING,
    PIPE_ACCESS_DUPLEX,
    PIPE_READMODE_BYTE,
    PIPE_REJECT_REMOTE_CLIENTS,
    PIPE_TYPE_BYTE,
    PIPE_UNLIMITED_INSTANCES,
    PIPE_WAIT,
    cancel_handle_io,
    cancel_thread_io,
    close_handle,
    create_security_attributes,
    free_security_descriptor,
    kernel32,
    read_handle,
    require_windows,
    write_handle,
)
from agent.ipc.framing import FrameError, read_frame, write_frame


class NamedPipeServer:
    """Local-only Windows Named Pipe server; policy logic remains in its handler."""

    def __init__(
        self,
        pipe_name: str,
        max_message_bytes: int,
        exchange_timeout_seconds: float = 5.0,
    ):
        self._pipe_name = pipe_name
        self._max_message_bytes = max_message_bytes
        self._exchange_timeout_seconds = exchange_timeout_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_handle: int | None = None
        self._handle_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._startup_error: BaseException | None = None
        self._workers: dict[threading.Thread, threading.Event] = {}

    def start(self, handler: Callable[[bytes], bytes]) -> None:
        require_windows()
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Named Pipe server is already running")
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._serve,
            args=(handler,),
            name="eecp-named-pipe-server",
            daemon=True,
        )
        self._thread.start()
        if not self._ready_event.wait(timeout=2.0):
            raise OSError("Named Pipe server did not become ready")
        if self._startup_error is not None:
            raise OSError(f"Named Pipe server failed to start: {self._startup_error}")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_or_cancel()
        with self._handle_lock:
            workers = list(self._workers.items())
        for worker, cancelled in workers:
            cancelled.set()
            cancel_thread_io(worker.native_id)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _exchange(
        self, handle: int, handler: Callable[[bytes], bytes], cancelled: threading.Event
    ) -> None:
        try:
            if cancelled.is_set() or self._stop_event.is_set():
                return
            request = read_frame(
                lambda size, pipe=handle: read_handle(pipe, size),
                self._max_message_bytes,
            )
            if cancelled.is_set() or self._stop_event.is_set():
                return
            response = handler(request)
            if cancelled.is_set() or self._stop_event.is_set():
                return
            write_frame(
                lambda chunk, pipe=handle: write_handle(pipe, chunk),
                response,
                self._max_message_bytes,
            )
            # Flush may wait for the peer to read. Keep it in the cancellable
            # worker, and keep ownership until all I/O has actually stopped.
            if not cancelled.is_set() and not self._stop_event.is_set():
                kernel32().FlushFileBuffers(handle)
        except (FrameError, OSError):
            pass
        except Exception:
            pass
        finally:
            with suppress(OSError):
                kernel32().DisconnectNamedPipe(handle)
            close_handle(handle)
            with self._handle_lock:
                self._workers.pop(threading.current_thread(), None)

    def _serve(self, handler: Callable[[bytes], bytes]) -> None:
        try:
            while not self._stop_event.is_set():
                handle = self._create_instance()
                with self._handle_lock:
                    self._active_handle = handle
                self._ready_event.set()
                handed_off = False
                try:
                    if not self._connect(handle):
                        continue
                    if self._stop_event.is_set():
                        continue
                    cancelled = threading.Event()
                    worker = threading.Thread(
                        target=self._exchange,
                        args=(handle, handler, cancelled),
                        name="eecp-pipe-exchange",
                        daemon=True,
                    )
                    with self._handle_lock:
                        self._workers[worker] = cancelled
                        self._active_handle = None
                    worker.start()
                    handed_off = True
                    deadline = time.monotonic() + self._exchange_timeout_seconds
                    while worker.is_alive() and not self._stop_event.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        worker.join(timeout=min(0.05, remaining))
                    if worker.is_alive():
                        cancelled.set()
                        cancel_deadline = time.monotonic() + 0.2
                        while worker.is_alive() and time.monotonic() < cancel_deadline:
                            cancel_thread_io(worker.native_id)
                            worker.join(timeout=0.01)
                finally:
                    with self._handle_lock:
                        if self._active_handle == handle:
                            self._active_handle = None
                    if not handed_off:
                        with suppress(OSError):
                            kernel32().DisconnectNamedPipe(handle)
                        close_handle(handle)
        except Exception as exc:
            self._startup_error = exc
            self._ready_event.set()

    def _create_instance(self) -> int:
        attributes, descriptor = create_security_attributes()
        try:
            handle = kernel32().CreateNamedPipeW(
                self._pipe_name,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                PIPE_UNLIMITED_INSTANCES,
                self._max_message_bytes + 4,
                self._max_message_bytes + 4,
                0,
                ctypes.byref(attributes),
            )
        finally:
            free_security_descriptor(descriptor)
        if handle == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def _connect(self, handle: int) -> bool:
        if kernel32().ConnectNamedPipe(handle, None):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_PIPE_CONNECTED:
            return True
        if error == ERROR_OPERATION_ABORTED and self._stop_event.is_set():
            return False
        raise ctypes.WinError(error)

    def _wake_or_cancel(self) -> None:
        api = kernel32()
        if api.WaitNamedPipeW(self._pipe_name, 100):
            wake_handle = api.CreateFileW(
                self._pipe_name,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if wake_handle != INVALID_HANDLE_VALUE:
                close_handle(int(wake_handle))
                return
        with self._handle_lock:
            handle = self._active_handle
        if handle is not None:
            cancel_handle_io(handle)
            if self._thread is not None:
                cancel_thread_io(self._thread.native_id)
