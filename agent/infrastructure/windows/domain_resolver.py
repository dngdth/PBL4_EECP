from __future__ import annotations

import ipaddress
import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from contracts.v2.policy import validate_hostname


class DomainResolver(Protocol):
    def resolve(self, domains: Iterable[str]) -> Mapping[str, Iterable[str]]: ...


# Keep policy data on stdin, never interpolate it into PowerShell source. Read
# cached addresses BEFORE hosts/flushdns can erase evidence of existing flows.
# NoHostsFile is essential on refresh: getaddrinfo would resolve our own sinkhole.
DNS_SNAPSHOT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$domains = ConvertFrom-Json ([Console]::In.ReadToEnd())
$records = [System.Collections.Generic.List[object]]::new()
$names = [System.Collections.Generic.HashSet[string]]::new()
foreach ($domain in $domains) {
    [void]$names.Add($domain)
    [void]$names.Add('www.' + $domain)
}
foreach ($entry in @(Get-DnsClientCache -ErrorAction Stop)) {
    foreach ($name in @($entry.Entry, $entry.RecordName)) {
        if (-not $name) { continue }
        $name = $name.ToLowerInvariant().TrimEnd('.')
        foreach ($domain in $domains) {
            if ($name -eq $domain -or $name.EndsWith('.' + $domain)) {
                if ($entry.RecordType -in @(1, 28) -and $entry.Data) {
                    $records.Add(@{name=$name; address=[string]$entry.Data})
                }
                break
            }
        }
    }
}
foreach ($name in $names) {
    try {
        foreach ($answer in @(Resolve-DnsName -Name $name -Type A_AAAA `
            -DnsOnly -NoHostsFile -QuickTimeout -ErrorAction Stop)) {
            if ($answer.IPAddress) {
                $records.Add(@{name=$name; address=[string]$answer.IPAddress})
            }
        }
    } catch {
        # One missing www alias / unavailable DNS server must not discard cached
        # addresses. Python checks coverage of every required domain below.
    }
}
ConvertTo-Json -InputObject @($records.ToArray()) -Compress
"""


class WindowsDomainResolver:
    """Snapshot cached subdomains and query real A/AAAA records, bypassing hosts."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 8.0,
    ):
        self._runner = runner
        self._timeout = timeout_seconds

    def resolve(self, domains: Iterable[str]) -> dict[str, tuple[str, ...]]:
        requested = tuple(sorted({validate_hostname(domain).lower() for domain in domains}))
        if not requested:
            return {}
        try:
            result = self._runner(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    DNS_SNAPSHOT_SCRIPT,
                ],
                input=json.dumps(requested),
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise OSError("domain traffic DNS discovery timed out") from exc
        if result.returncode != 0:
            raise OSError("cannot discover domain traffic addresses from Windows DNS")
        try:
            records = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise OSError("Windows DNS discovery returned invalid JSON") from exc
        if not isinstance(records, list):
            raise OSError("Windows DNS discovery returned invalid records")
        addresses: dict[str, set[str]] = {domain: set() for domain in requested}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("name"), str):
                raise OSError("Windows DNS discovery returned an invalid record")
            hostname = record["name"].lower().rstrip(".")
            try:
                address = traffic_address(record.get("address", ""))
            except ValueError as exc:
                raise OSError("Windows DNS discovery returned an invalid address") from exc
            if address is None:
                continue
            for domain in requested:
                if hostname == domain or hostname.endswith(f".{domain}"):
                    addresses[domain].add(address)
        return {domain: tuple(sorted(values)) for domain, values in addresses.items()}


def traffic_address(value: str) -> str | None:
    """Exclude hosts sinkholes and non-unicast addresses from network blocks."""
    address = ipaddress.ip_address(value)
    if (
        address.is_loopback
        or address.is_unspecified
        or address.is_multicast
        or address.is_link_local
    ):
        return None
    return str(address)
