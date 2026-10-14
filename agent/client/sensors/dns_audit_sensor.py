from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

from agent.domain.policy import CATEGORY_DOMAINS, parse_policy_payload

DNS_CACHE_COMMAND = (
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "Get-DnsClientCache | Select-Object -ExpandProperty Entry | "
    "ConvertTo-Json -Compress",
)


class DnsAuditSensor:
    """Observe blocked domains in the Windows DNS cache without enforcing policy."""

    def __init__(
        self,
        report: Callable[[str, str], None],
        *,
        dns_provider: Callable[[], Iterable[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        debounce_seconds: float = 60.0,
    ):
        self._report = report
        self._dns_provider = dns_provider or windows_dns_cache_names
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._session_id: str | None = None
        self._blocked_domains: set[str] = set()
        self._last_reported: dict[str, float] = {}

    def activate(self, session_id: str, payload: dict[str, Any]) -> None:
        specification = parse_policy_payload(payload)
        blocked_domains = {
            domain
            for category in specification.blocked_categories
            for domain in CATEGORY_DOMAINS[category]
        } | set(specification.blocked_domains)
        if self._session_id != session_id or self._blocked_domains != blocked_domains:
            self._last_reported.clear()
        self._session_id = session_id
        self._blocked_domains = blocked_domains

    def deactivate(self) -> None:
        self._session_id = None
        self._blocked_domains.clear()
        self._last_reported.clear()

    def poll(self) -> None:
        if self._session_id is None or not self._blocked_domains:
            return
        now = self._clock()
        matches = {
            blocked_domain
            for value in self._dns_provider()
            if (hostname := _normalize_hostname(value))
            and (blocked_domain := _blocked_domain_for(hostname, self._blocked_domains))
        }
        for hostname in sorted(matches):
            last_reported = self._last_reported.get(hostname)
            if last_reported is None or now - last_reported >= self._debounce_seconds:
                self._report(self._session_id, hostname)
                self._last_reported[hostname] = now


def windows_dns_cache_names(
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[str, ...]:
    if sys.platform != "win32":
        return ()
    result = runner(
        list(DNS_CACHE_COMMAND),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        raise OSError("cannot read Windows DNS cache")
    output = result.stdout.strip()
    if not output:
        return ()
    try:
        values = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("Windows DNS cache returned invalid data") from exc
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise OSError("Windows DNS cache returned invalid data")
    return tuple(
        hostname
        for value in values
        if (hostname := _normalize_hostname(value))
    )


def _normalize_hostname(value: str) -> str:
    return value.strip().lower().rstrip(".")


def _blocked_domain_for(hostname: str, blocked_domains: set[str]) -> str | None:
    matches = [
        domain
        for domain in blocked_domains
        if hostname == domain or hostname.endswith(f".{domain}")
    ]
    return max(matches, key=len, default=None)
