from __future__ import annotations

import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from agent.domain.identity import WorkstationIdentity


def collect_identity(
    agent_id: str,
    agent_version: str,
    server_url: str,
    hostname_getter: Callable[[], str] = socket.gethostname,
    resolver: Callable[[str], str] = socket.gethostbyname,
    socket_factory: Callable[..., socket.socket] = socket.socket,
) -> WorkstationIdentity:
    hostname = hostname_getter()
    server_hostname = urlsplit(server_url).hostname or "192.168.3.50"
    try:
        with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect((server_hostname, 80))
            ip_address = connection.getsockname()[0]
    except OSError:
        ip_address = resolver(hostname)
    return WorkstationIdentity(agent_id, hostname, ip_address, agent_version)
