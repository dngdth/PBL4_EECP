from __future__ import annotations

import ipaddress
import json
import subprocess
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from contracts.v2.policy import validate_hostname


class DomainResolver(Protocol):
    def resolve(self, domains: Iterable[str]) -> Mapping[str, Iterable[str]]: ...


# Terminal CNAME addresses can belong to a CDN outside the blocked suffix.
# Snapshot everything before hosts/flushdns; only follow forward DNS links.
DNS_CACHE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$records = [System.Collections.Generic.List[object]]::new()
$cachedNames = [System.Collections.Generic.HashSet[string]]::new()
foreach ($entry in @(Get-DnsClientCache -ErrorAction Stop)) {
    $recordType = $entry.RecordType
    if ($null -eq $recordType) { $recordType = $entry.Type }
    $kind = ([string]$recordType).ToUpperInvariant()
    foreach ($rawName in @($entry.Entry, $entry.RecordName, $entry.Name)) {
        if (-not $rawName) { continue }
        $name = ([string]$rawName).ToLowerInvariant().TrimEnd('.')
        [void]$cachedNames.Add($name)
        if (-not $entry.Data) { continue }
        if ($kind -in @('1', '28', 'A', 'AAAA')) {
            $records.Add(@{name=$name; address=[string]$entry.Data; source='windows-cache'})
        } elseif ($kind -in @('5', 'CNAME')) {
            $records.Add(@{name=$name; cname=[string]$entry.Data; source='windows-cache'})
        }
    }
}
"""

DNS_SNAPSHOT_SCRIPT = (
    DNS_CACHE_SCRIPT
    + r"""
$request = ConvertFrom-Json ([Console]::In.ReadToEnd())
$names = [System.Collections.Generic.HashSet[string]]::new()
foreach ($domain in $request.domains) {
    [void]$names.Add($domain)
    [void]$names.Add('www.' + $domain)
}
$extra = 0
foreach ($name in @($request.known_names) + @($cachedNames)) {
    if ($extra -ge 256) { break }
    foreach ($domain in $request.domains) {
        if ($name -eq $domain -or $name.EndsWith('.' + $domain)) {
            if ($names.Add($name)) { $extra++ }
            break
        }
    }
}
foreach ($name in $names) {
    try {
        $arguments = @{Name=$name; Type='A_AAAA'; DnsOnly=$true; NoHostsFile=$true;
                       QuickTimeout=$true; ErrorAction='Stop'}
        foreach ($answer in @(Resolve-DnsName @arguments)) {
            if ($answer.IPAddress) {
                $records.Add(@{name=$name; address=[string]$answer.IPAddress; source='dns-query'})
            }
        }
    } catch {
        # Failed queries must not discard cached addresses of existing flows.
    }
}
ConvertTo-Json -InputObject @($records.ToArray()) -Compress
"""
)
DNS_OBSERVE_SCRIPT = (
    DNS_CACHE_SCRIPT + "\nConvertTo-Json -InputObject @($records.ToArray()) -Compress"
)


@dataclass(frozen=True, slots=True)
class DnsRecord:
    name: str
    kind: str
    value: str


class WindowsDomainResolver:
    """Follow forward CNAME chains and retain bounded pre-policy DNS history.

    The Service opts into passive cache observation. History stays in memory;
    only evidence for active blocked domains is persisted by the enforcer.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 8.0,
        observe_cache: bool = False,
        history_seconds: float = 3600.0,
        max_history_records: int = 10000,
        clock: Callable[[], float] = time.monotonic,
    ):
        if timeout_seconds <= 0 or history_seconds <= 0 or max_history_records < 1:
            raise ValueError("DNS discovery limits must be positive")
        self._runner, self._timeout, self._clock = runner, timeout_seconds, clock
        self._observe_cache = observe_cache
        self._history_seconds, self._max_history_records = history_seconds, max_history_records
        self._history: dict[DnsRecord, float] = {}
        self.last_evidence: dict[str, list[dict]] = {}

    def observe(self) -> None:
        """Read cache without queries, including before an exam starts."""
        if self._observe_cache:
            self._remember(self._snapshot(DNS_OBSERVE_SCRIPT, None, min(3.0, self._timeout)))

    def resolve(self, domains: Iterable[str]) -> dict[str, tuple[str, ...]]:
        requested = self._requested(domains)
        if not requested:
            self.last_evidence = {}
            return {}
        self._prune()
        known_names = sorted(
            {
                record.name
                for record in self._history
                if any(_matches(record.name, domain) for domain in requested)
            }
        )[:256]
        records = self._snapshot(
            DNS_SNAPSHOT_SCRIPT,
            {"domains": requested, "known_names": known_names},
            self._timeout,
        )
        self._remember(records)
        return self._addresses(requested, records)

    def cached_addresses(self, domains: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """No queries: include evidence captured during Service maintenance."""
        self._prune()
        return self._addresses(self._requested(domains), {})

    @staticmethod
    def _requested(domains: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({validate_hostname(domain).lower() for domain in domains}))

    def _snapshot(self, script: str, payload: dict | None, timeout: float) -> dict[DnsRecord, str]:
        try:
            result = self._runner(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                input=json.dumps(payload) if payload is not None else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise OSError("domain traffic DNS discovery timed out") from exc
        if result.returncode != 0:
            raise OSError("cannot discover domain traffic addresses from Windows DNS")
        try:
            raw_records = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise OSError("Windows DNS discovery returned invalid JSON") from exc
        if not isinstance(raw_records, list):
            raise OSError("Windows DNS discovery returned invalid records")
        records: dict[DnsRecord, str] = {}
        for raw in raw_records:
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                raise OSError("Windows DNS discovery returned an invalid record")
            if ("address" in raw) == ("cname" in raw):
                raise OSError("Windows DNS discovery returned an invalid record")
            try:
                name = validate_hostname(raw["name"].lower().rstrip("."))
            except ValueError:
                continue  # Other cached names can fall outside policy's hostname grammar.
            if "cname" in raw:
                try:
                    target = validate_hostname(raw["cname"].lower().rstrip("."))
                except (ValueError, AttributeError):
                    continue
                record = DnsRecord(name, "cname", target)
            else:
                if not isinstance(raw["address"], str):
                    raise OSError("Windows DNS discovery returned an invalid address")
                try:
                    address = traffic_address(raw["address"])
                except (ValueError, TypeError) as exc:
                    raise OSError("Windows DNS discovery returned an invalid address") from exc
                if address is None:
                    continue
                record = DnsRecord(name, "address", address)
            source = raw.get("source", "windows-dns")
            if not isinstance(source, str) or source not in {
                "windows-cache",
                "dns-query",
                "windows-dns",
            }:
                raise OSError("Windows DNS discovery returned an invalid source")
            records[record] = source
        return records

    def _remember(self, records: Mapping[DnsRecord, str]) -> None:
        self._history.update(dict.fromkeys(records, self._clock()))
        self._prune()

    def _prune(self) -> None:
        cutoff = self._clock() - self._history_seconds
        self._history = {
            record: seen
            for record, seen in sorted(self._history.items(), key=lambda item: item[1])
            if seen > cutoff
        }
        while len(self._history) > self._max_history_records:
            del self._history[next(iter(self._history))]

    def _addresses(
        self,
        domains: tuple[str, ...],
        current: Mapping[DnsRecord, str],
    ) -> dict[str, tuple[str, ...]]:
        by_name: dict[str, list[DnsRecord]] = defaultdict(list)
        # Current answers still participate if the bounded history is full.
        for record in self._history.keys() | current.keys():
            by_name[record.name].append(record)
        addresses: dict[str, tuple[str, ...]] = {}
        evidence: dict[str, list[dict]] = {}
        for domain in domains:
            queue = deque((name, (name,)) for name in sorted(by_name) if _matches(name, domain))
            visited, found, observations = set(), set(), []
            while queue:
                name, chain = queue.popleft()
                if name in visited:
                    continue
                visited.add(name)
                for record in sorted(
                    by_name.get(name, ()), key=lambda item: (item.kind, item.value)
                ):
                    if record.kind == "cname":
                        if len(chain) < 32 and record.value not in visited:
                            queue.append((record.value, (*chain, record.value)))
                    else:
                        found.add(record.value)
                        observations.append(
                            {
                                "hostname": name,
                                "address": record.value,
                                "source": current.get(record, "dns-history"),
                                "cname_chain": list(chain),
                            }
                        )
            addresses[domain], evidence[domain] = tuple(sorted(found)), observations
        self.last_evidence = evidence
        return addresses


def _matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


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
