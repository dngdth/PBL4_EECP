"""Two-machine smoke test for traffic on connections opened BEFORE policy apply.

Run the echo peer on a second test machine, then run the client elevated. This
never edits the real hosts file and removes its exact Firewall rules in finally.
See docs/domain-traffic-enforcement.md for commands and the acceptance matrix.
"""

from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

from agent.config import GATEWAY_URL
from agent.infrastructure.policy_enforcement import WindowsPolicyEnforcer
from agent.infrastructure.windows.firewall_enforcer import (
    WindowsFirewallEnforcer,
    domain_traffic_rules,
    resolve_gateway_addresses,
)
from contracts.v2 import PolicyRules, compute_policy_hash

MESSAGE = b"EECP domain-traffic verification\n"


class TcpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            while data := self.request.recv(4096):
                self.request.sendall(data)
        except OSError:
            pass


class UdpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, connection = self.request
        connection.sendto(data, self.client_address)


def serve(bind: str, port: int) -> None:
    family = socket.AF_INET6 if ipaddress.ip_address(bind).version == 6 else socket.AF_INET

    class TcpServer(socketserver.ThreadingTCPServer):
        address_family = family
        allow_reuse_address = True
        daemon_threads = True

    class UdpServer(socketserver.ThreadingUDPServer):
        address_family = family
        allow_reuse_address = True
        daemon_threads = True

    with TcpServer((bind, port), TcpEcho) as tcp, UdpServer((bind, port), UdpEcho) as udp:
        threading.Thread(target=udp.serve_forever, daemon=True).start()
        print(f"Echo peer listening on {bind}:{port} for TCP and UDP; Ctrl+C to stop")
        try:
            tcp.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            udp.shutdown()


def connect(peer: str, port: int, kind: int) -> socket.socket:
    family = socket.AF_INET6 if ipaddress.ip_address(peer).version == 6 else socket.AF_INET
    connection = socket.socket(family, kind)
    connection.settimeout(2.0)
    try:
        connection.connect((peer, port))
    except BaseException:
        connection.close()
        raise
    return connection


def echo(connection: socket.socket) -> None:
    connection.sendall(MESSAGE)
    received = b""
    while len(received) < len(MESSAGE):
        data = connection.recv(4096)
        if not data:
            break
        received += data
    if received != MESSAGE:
        raise RuntimeError("echo peer did not return the verification message")


def assert_blocked(connection: socket.socket) -> None:
    try:
        connection.sendall(MESSAGE)
        received = connection.recv(4096)
    except OSError:
        return
    if received:
        raise AssertionError("FAIL: application data still passes through an existing connection")


def verify(peer: str, port: int) -> None:
    if sys.platform != "win32" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit("Run the client from an Administrator terminal on Windows")
    address = ipaddress.ip_address(peer)
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise SystemExit("Use a second test machine's unicast IP, not loopback")
    profiles = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "@(Get-NetFirewallProfile | ForEach-Object { [bool]$_.Enabled }) | "
            "ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    if json.loads(profiles.stdout) != [True, True, True]:
        raise SystemExit("Enable the Windows Firewall profiles on this test client first")
    # Two sockets are established and exchange data before any rule is installed.
    with (
        connect(peer, port, socket.SOCK_STREAM) as tcp,
        connect(peer, port, socket.SOCK_DGRAM) as udp,
    ):
        echo(tcp)
        echo(udp)
        print("PASS: TCP and UDP exchange data before policy apply")
        session_id = f"TRAFFIC-VERIFY-{uuid.uuid4().hex}"
        firewall = WindowsFirewallEnforcer(resolve_gateway_addresses(GATEWAY_URL))
        cleanup = domain_traffic_rules(firewall.plan(session_id, [str(address)], []))
        rules = {"network": {"blocked_domains": ["blocked.example"]}}
        policy = {
            "profile": "TRAFFIC_VERIFY",
            "version": 1,
            "session_id": session_id,
            "policy_hash": compute_policy_hash(
                "TRAFFIC_VERIFY", 1, PolicyRules.model_validate(rules)
            ),
            "rules": rules,
        }

        def runner(args, **kwargs):
            # A synthetic hosts file needs no system-wide DNS cache flush.
            if args == ["ipconfig", "/flushdns"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.run(args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="eecp-traffic-") as temporary:
            directory = Path(temporary)
            hosts = directory / "hosts"
            hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
            enforcer = WindowsPolicyEnforcer(
                directory / "policy.json",
                hosts_path=hosts,
                runner=runner,
                firewall=firewall,
                domain_resolver=SimpleNamespace(
                    resolve=lambda domains: {domain: (str(address),) for domain in domains}
                ),
            )
            try:
                enforcer.apply(policy)
                assert_blocked(tcp)
                assert_blocked(udp)
                print("PASS: existing TCP and UDP sockets cannot exchange new application data")
                try:
                    with connect(peer, port, socket.SOCK_STREAM) as new_tcp:
                        assert_blocked(new_tcp)
                except OSError:
                    pass
                print("PASS: new TCP traffic is blocked")
                enforcer.restore()
                with (
                    connect(peer, port, socket.SOCK_STREAM) as restored_tcp,
                    connect(peer, port, socket.SOCK_DGRAM) as restored_udp,
                ):
                    echo(restored_tcp)
                    echo(restored_udp)
                print("PASS: TCP and UDP work after restore")
            finally:
                # Even a partially failed apply must not leave the test rules.
                try:
                    enforcer.restore()
                finally:
                    firewall.remove_rules(rule.name for rule in cleanup)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true", help="start the second machine's echo peer")
    mode.add_argument("--peer", help="IP of a second test machine, never the Gateway")
    parser.add_argument("--bind", default="0.0.0.0", help="echo peer's listening address")
    parser.add_argument("--port", type=int, default=19123)
    args = parser.parse_args()
    if args.serve:
        serve(args.bind, args.port)
    else:
        verify(args.peer, args.port)


if __name__ == "__main__":
    main()
