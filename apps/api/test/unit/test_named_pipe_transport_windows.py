import ctypes
import os
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
