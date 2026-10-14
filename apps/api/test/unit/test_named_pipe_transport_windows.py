import os
import uuid

import pytest

from agent.ipc.named_pipe_client import (
    IpcTimeoutError,
    NamedPipeClient,
    NamedPipeClientConfig,
)
from agent.ipc.named_pipe_server import NamedPipeServer


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
        assert client.request(b"first") == b"FIRST"
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
