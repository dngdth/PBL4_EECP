from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from agent.domain.policy import parse_policy_payload
from agent.infrastructure.policy_enforcement import CATEGORY_DOMAINS

REPORT_DEBOUNCE_SECONDS = 15.0
CAPTURE_BYTES = 16_384


class BlockedDomainMonitor:
    """Observe HTTP/TLS requests redirected to loopback by the managed hosts policy."""

    def __init__(
        self,
        report: Callable[[str, str], None],
        log: Callable[[str], None] = print,
        clock: Callable[[], float] = time.monotonic,
        debounce_seconds: float = REPORT_DEBOUNCE_SECONDS,
    ):
        self._report = report
        self._log = log
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._blocked_domains: set[str] = set()
        self._last_reported: dict[tuple[str, str], float] = {}

    def start(self) -> None:
        for port in (80, 443):
            threading.Thread(
                target=self._serve,
                args=(port,),
                name=f"eecp-policy-monitor-{port}",
                daemon=True,
            ).start()

    def activate(self, session_id: str, payload: dict[str, Any]) -> None:
        specification = parse_policy_payload(payload)
        blocked_domains = {
            domain
            for category in specification.blocked_categories
            for domain in CATEGORY_DOMAINS[category]
        }
        with self._lock:
            if (
                self._session_id == session_id
                and self._blocked_domains == blocked_domains
            ):
                return
            self._session_id = session_id
            self._blocked_domains = blocked_domains
            self._last_reported.clear()

    def deactivate(self) -> None:
        with self._lock:
            self._session_id = None
            self._blocked_domains.clear()
            self._last_reported.clear()

    def record_attempt(self, hostname: str) -> None:
        normalized = hostname.strip().lower().rstrip(".")
        with self._lock:
            session_id = self._session_id
            if session_id is None or not _matches_blocked_domain(
                normalized, self._blocked_domains
            ):
                return
            key = (session_id, normalized)
            now = self._clock()
            previous = self._last_reported.get(key)
            if previous is not None and now - previous < self._debounce_seconds:
                return
            self._last_reported[key] = now

        try:
            self._report(session_id, normalized)
            self._log(f"Policy violation reported: {normalized}")
        except OSError as exc:
            self._log(f"Policy violation report failed: {exc}")

    def _serve(self, port: int) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(16)
        except OSError as exc:
            listener.close()
            self._log(f"Policy monitor could not listen on 127.0.0.1:{port}: {exc}")
            return

        while True:
            try:
                connection, _address = listener.accept()
                threading.Thread(
                    target=self._inspect_connection,
                    args=(connection,),
                    daemon=True,
                ).start()
            except OSError as exc:
                self._log(f"Policy monitor listener failed on port {port}: {exc}")
                return

    def _inspect_connection(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(2)
            try:
                data = connection.recv(CAPTURE_BYTES)
            except OSError:
                return
            hostname = extract_requested_hostname(data)
            if hostname:
                self.record_attempt(hostname)


def extract_requested_hostname(data: bytes) -> str | None:
    return _http_hostname(data) or _tls_sni_hostname(data)


def _http_hostname(data: bytes) -> str | None:
    try:
        headers = data.decode("iso-8859-1").split("\r\n")
    except UnicodeDecodeError:
        return None
    for header in headers[1:]:
        name, separator, value = header.partition(":")
        if separator and name.strip().lower() == "host":
            return value.strip().split(":", 1)[0].lower()
    return None


def _tls_sni_hostname(data: bytes) -> str | None:
    # Minimal TLS ClientHello parser. It reads only the plaintext SNI extension.
    try:
        if len(data) < 5 or data[0] != 0x16:
            return None
        record_length = int.from_bytes(data[3:5], "big")
        body = memoryview(data)[5 : 5 + record_length]
        if len(body) < 42 or body[0] != 0x01:
            return None
        offset = 4 + 2 + 32
        session_length = body[offset]
        offset += 1 + session_length
        cipher_length = int.from_bytes(body[offset : offset + 2], "big")
        offset += 2 + cipher_length
        compression_length = body[offset]
        offset += 1 + compression_length
        extensions_length = int.from_bytes(body[offset : offset + 2], "big")
        offset += 2
        extensions_end = min(offset + extensions_length, len(body))
        while offset + 4 <= extensions_end:
            extension_type = int.from_bytes(body[offset : offset + 2], "big")
            extension_length = int.from_bytes(body[offset + 2 : offset + 4], "big")
            offset += 4
            extension = body[offset : offset + extension_length]
            offset += extension_length
            if extension_type != 0 or len(extension) < 5:
                continue
            name_length = int.from_bytes(extension[3:5], "big")
            return bytes(extension[5 : 5 + name_length]).decode("ascii").lower()
    except (IndexError, UnicodeDecodeError, ValueError):
        return None
    return None


def _matches_blocked_domain(hostname: str, blocked_domains: set[str]) -> bool:
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in blocked_domains
    )
