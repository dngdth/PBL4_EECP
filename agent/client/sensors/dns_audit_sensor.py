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
    "$ErrorActionPreference = 'Stop'; "
    "@(Get-DnsClientCache | Select-Object -ExpandProperty Entry) | "
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
        log: Callable[[str], None] = print,
    ):
        self._report = report
        self._dns_provider = dns_provider or windows_dns_cache_names
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._log = log
        self._session_id: str | None = None
        self._blocked_domains: set[str] = set()
        self._last_reported: dict[str, float] = {}
        self._provider_ready_logged = False

    def activate(self, session_id: str, payload: dict[str, Any]) -> None:
        specification = parse_policy_payload(payload)
        blocked_domains = {
            domain
            for category in specification.blocked_categories
            for domain in CATEGORY_DOMAINS[category]
        } | set(specification.blocked_domains)
        changed = self._session_id != session_id or self._blocked_domains != blocked_domains
        if changed:
            self._last_reported.clear()
            self._provider_ready_logged = False
        self._session_id = session_id
        self._blocked_domains = blocked_domains
        if changed:
            self._log(
                "event=AUDIT_SENSOR_ACTIVATED "
                f"session_id={session_id} blocked_domain_count={len(blocked_domains)}"
            )

    def deactivate(self) -> None:
        previous_session = self._session_id
        self._session_id = None
        self._blocked_domains.clear()
        self._last_reported.clear()
        self._provider_ready_logged = False
        if previous_session is not None:
            self._log(
                f"event=AUDIT_SENSOR_DEACTIVATED session_id={previous_session}"
            )

    def poll(self) -> None:
        if self._session_id is None or not self._blocked_domains:
            return
        now = self._clock()
        cached_names = tuple(self._dns_provider())
        if not self._provider_ready_logged:
            self._log(
                "event=AUDIT_DNS_PROVIDER_READY "
                f"session_id={self._session_id} cached_name_count={len(cached_names)}"
            )
            self._provider_ready_logged = True
        matches = {
            blocked_domain
            for value in cached_names
            if (hostname := _normalize_hostname(value))
            and (blocked_domain := _blocked_domain_for(hostname, self._blocked_domains))
        }
        if matches:
            self._log(
                "event=AUDIT_DNS_MATCH "
                f"session_id={self._session_id} matched_domain_count={len(matches)}"
            )
        for hostname in sorted(matches):
            last_reported = self._last_reported.get(hostname)
            if last_reported is None or now - last_reported >= self._debounce_seconds:
                self._report(self._session_id, hostname)
                self._last_reported[hostname] = now
                self._log(
                    "event=AUDIT_EVENT_CREATED "
                    f"session_id={self._session_id} destination={hostname} action=OBSERVED"
                )


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
    stderr = getattr(result, "stderr", "") or ""
    if result.returncode != 0 or stderr.strip():
        raise OSError("cannot read Windows DNS cache; provider command failed")
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
