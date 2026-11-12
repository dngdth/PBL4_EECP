from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from agent.ipc._windows import (
    GENERIC_READ,
    GENERIC_WRITE,
    INVALID_HANDLE_VALUE,
    OPEN_EXISTING,
    cancel_thread_io,
    close_handle,
    kernel32,
    read_handle,
    require_windows,
    write_handle,
)
from agent.ipc.framing import read_frame, write_frame


class IpcTransportError(OSError):
    pass


class IpcTimeoutError(IpcTransportError):
    pass


class RequestTransport(Protocol):
    def request(self, payload: bytes) -> bytes: ...


@dataclass(frozen=True)
class NamedPipeClientConfig:
    pipe_name: str
    connect_timeout_seconds: float
    request_timeout_seconds: float
    max_message_bytes: int


class NamedPipeClient:
    """One-request-per-connection client with bounded wait and reconnect semantics."""

    def __init__(self, config: NamedPipeClientConfig):
        self._config = config
        self._request_lock = threading.Lock()

    def request(self, payload: bytes) -> bytes:
        require_windows()
        outcomes: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)
        with self._request_lock:
            cancelled = threading.Event()
            worker = threading.Thread(
                target=self._exchange_worker,
                args=(payload, outcomes, cancelled),
                name="eecp-pipe-request",
                daemon=True,
            )
            worker.start()
            worker.join(self._config.request_timeout_seconds)
            if worker.is_alive():
                cancelled.set()
                # The worker alone owns and closes its connection. Closing it
                # here could let a late worker use a recycled Windows handle.
                deadline = time.monotonic() + 1.0
                while worker.is_alive() and time.monotonic() < deadline:
                    cancel_thread_io(worker.native_id)
                    worker.join(timeout=0.01)
                raise IpcTimeoutError("Named Pipe request timed out")
            outcome = outcomes.get_nowait()
            if isinstance(outcome, BaseException):
                if isinstance(outcome, IpcTransportError):
                    raise outcome
                raise IpcTransportError(f"Named Pipe request failed: {outcome}") from outcome
            return outcome

    def _exchange_worker(
        self,
        payload: bytes,
        outcomes: queue.Queue[bytes | BaseException],
        cancelled: threading.Event,
    ) -> None:
        handle: int | None = None
        try:
            handle = self._connect(cancelled)
            if cancelled.is_set():
                return
            write_frame(
                lambda chunk: write_handle(handle, chunk),
                payload,
                self._config.max_message_bytes,
            )
            if cancelled.is_set():
                return
            response = read_frame(
                lambda size: read_handle(handle, size),
                self._config.max_message_bytes,
            )
            outcomes.put_nowait(response)
        except BaseException as exc:
            outcomes.put_nowait(exc)
        finally:
            close_handle(handle)

    def _connect(self, cancelled: threading.Event) -> int:
        api = kernel32()
        deadline = time.monotonic() + self._config.connect_timeout_seconds
        while True:
            if cancelled.is_set():
                raise IpcTimeoutError("Named Pipe request cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IpcTimeoutError("EECP Agent Service pipe is unavailable")
            wait_ms = max(1, min(100, int(remaining * 1000)))
            if api.WaitNamedPipeW(self._config.pipe_name, wait_ms):
                handle = api.CreateFileW(
                    self._config.pipe_name,
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    None,
                    OPEN_EXISTING,
                    0,
                    None,
                )
                if handle != INVALID_HANDLE_VALUE:
                    return int(handle)
            time.sleep(min(0.05, remaining))
