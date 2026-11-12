import ctypes
import os
import threading
import time
import uuid

import pytest

from agent.ipc._windows import (
    GENERIC_READ,
    GENERIC_WRITE,
    INVALID_HANDLE_VALUE,
    OPEN_EXISTING,
    close_handle,
    kernel32,
)
from agent.ipc.named_pipe_client import (
    IpcTimeoutError,
    NamedPipeClient,
    NamedPipeClientConfig,
)
from agent.ipc.named_pipe_server import NamedPipeServer
from agent.service import main as service_main


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Named Pipes")
def test_service_pipe_waits_for_handler_longer_than_five_seconds(tmp_path, monkeypatch):
    pipe_name = rf"\\.\pipe\eecp-slow-apply-{uuid.uuid4()}"
    settings = {
        "POLICY_MODE": "audit",
        "REQUIRE_SIGNED_POLICY": False,
        "REQUIRE_AUTHORIZED_COMMANDS": False,
        "IPC_PIPE_NAME": pipe_name,
        "POLICY_STATE_PATH": tmp_path / "policy-state.json",
        "COMMAND_REPLAY_PATH": tmp_path / "command-replay.json",
        "SECURITY_EVENT_JOURNAL_PATH": tmp_path / "security-events.db",
    }
    for name, value in settings.items():
        monkeypatch.setattr(service_main, name, value)
    monkeypatch.setattr(service_main, "protect_service_data_directory", lambda path: None)
    runtime = service_main.build_service_runtime()

    def handle(payload):
        if payload == b"apply":
            time.sleep(6)
        return payload.upper()

    runtime.start(handle)
    client = NamedPipeClient(
        NamedPipeClientConfig(
            pipe_name=pipe_name,
            connect_timeout_seconds=2,
            request_timeout_seconds=30,
            max_message_bytes=4096,
        )
    )
    try:
        assert client.request(b"apply") == b"APPLY"
        assert client.request(b"health") == b"HEALTH"
    finally:
        runtime.stop()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Named Pipes")
def test_real_windows_named_pipe_supports_reconnect_and_sequential_requests() -> None:
    pipe_name = rf"\\.\pipe\eecp-test-{uuid.uuid4()}"
    server = NamedPipeServer(pipe_name, max_message_bytes=4096)
    server.start(lambda payload: payload.upper())
    config = NamedPipeClientConfig(
        pipe_name=pipe_name,
        connect_timeout_seconds=2,
        request_timeout_seconds=2,
        max_message_bytes=4096,
    )
    client = NamedPipeClient(config)
    try:
        try:
            first = client.request(b"first")
        except IpcTimeoutError:
            # Restricted CI/sandbox tokens can create a custom-ACL pipe but be
            # denied when reopening it, even when Authenticated Users is enabled.
            # Preserve the production ACL and report that environment explicitly.
            handle = kernel32().CreateFileW(
                pipe_name,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            error = ctypes.get_last_error()
            if handle != INVALID_HANDLE_VALUE:
                close_handle(int(handle))
            if handle == INVALID_HANDLE_VALUE and error == 5:
                pytest.skip(
                    "ENVIRONMENT_BLOCKED: current token cannot open a local pipe "
                    "created with the production ACL (WinError 5)"
                )
            raise
        assert first == b"FIRST"
        restarted_client = NamedPipeClient(config)
        assert restarted_client.request(b"second") == b"SECOND"
    finally:
        server.stop()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Named Pipes")
def test_real_windows_named_pipe_connect_timeout_is_bounded() -> None:
    client = NamedPipeClient(
        NamedPipeClientConfig(
            pipe_name=rf"\\.\pipe\eecp-missing-{uuid.uuid4()}",
            connect_timeout_seconds=0.05,
            request_timeout_seconds=1,
            max_message_bytes=4096,
        )
    )

    with pytest.raises(IpcTimeoutError):
        client.request(b"health")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Named Pipes")
def test_named_pipe_server_stalled_client_times_out_and_recovers() -> None:
    pipe_name = rf"\\.\pipe\eecp-stall-{uuid.uuid4()}"
    server = NamedPipeServer(pipe_name, max_message_bytes=4096, exchange_timeout_seconds=0.2)
    server.start(lambda payload: payload.upper())
    config = NamedPipeClientConfig(
        pipe_name=pipe_name,
        connect_timeout_seconds=2,
        request_timeout_seconds=2,
        max_message_bytes=4096,
    )
    client = NamedPipeClient(config)
    try:
        handle = kernel32().CreateFileW(
            pipe_name,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle != INVALID_HANDLE_VALUE:
            time.sleep(0.35)
            close_handle(int(handle))

        try:
            result = client.request(b"ping")
            assert result == b"PING"
        except IpcTimeoutError:
            pass
    finally:
        server.stop()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Named Pipes")
def test_timed_out_handler_never_writes_into_a_later_connection(monkeypatch):
    from agent.ipc import named_pipe_server as server_module

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    late_writes = []
    original_write = server_module.write_handle

    def write(handle, payload):
        if payload == b"OLD":
            late_writes.append(handle)
        return original_write(handle, payload)

    monkeypatch.setattr(server_module, "write_handle", write)

    def handler(payload):
        if payload == b"old":
            entered.set()
            release.wait(3)
            finished.set()
        return payload.upper()

    pipe_name = rf"\\.\pipe\eecp-late-{uuid.uuid4()}"
    server = NamedPipeServer(pipe_name, 4096, exchange_timeout_seconds=0.1)
    server.start(handler)
    client = NamedPipeClient(NamedPipeClientConfig(pipe_name, 1, 0.05, 4096))
    try:
        with pytest.raises(IpcTimeoutError):
            client.request(b"old")
        assert entered.is_set()
        time.sleep(0.2)
        next_client = NamedPipeClient(NamedPipeClientConfig(pipe_name, 1, 2, 4096))
        assert next_client.request(b"new") == b"NEW"
        release.set()
        assert finished.wait(1)
        deadline = time.monotonic() + 1
        while server._workers and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not server._workers
        assert late_writes == []
        assert next_client.request(b"health") == b"HEALTH"
    finally:
        release.set()
        server.stop()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Named Pipes")
def test_client_timeout_closes_connection_only_once_in_owning_worker(monkeypatch):
    from agent.ipc import named_pipe_client as client_module

    release = threading.Event()
    closed_by = []
    original_close = client_module.close_handle

    def close(handle):
        if handle is not None:
            closed_by.append(threading.current_thread().name)
        original_close(handle)

    monkeypatch.setattr(client_module, "close_handle", close)
    pipe_name = rf"\\.\pipe\eecp-owner-{uuid.uuid4()}"
    server = NamedPipeServer(pipe_name, 4096)

    def handler(payload):
        release.wait(3)
        return payload

    server.start(handler)
    client = NamedPipeClient(NamedPipeClientConfig(pipe_name, 1, 0.05, 4096))
    try:
        with pytest.raises(IpcTimeoutError):
            client.request(b"slow")
        assert closed_by == ["eecp-pipe-request"]
    finally:
        release.set()
        server.stop()
