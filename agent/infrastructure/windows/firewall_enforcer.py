from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlparse

RULE_PREFIX = "EECP-"
LOGGER = logging.getLogger(__name__)
FIREWALL_COMMAND_TIMEOUT_SECONDS = 30.0

# Missing exact names are expected during reconciliation. Permission/provider
# errors must propagate instead of being mistaken for an absent rule.
_SCRIPT_HEADER = (
    "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
    "$request=ConvertFrom-Json ([Console]::In.ReadToEnd());"
    "function Get-EecpRule([string[]]$Names){"
    "if($Names.Count -eq 0){return};"
    "$lookupErrors=@();"
    "$found=@(Get-NetFirewallRule -Name $Names -ErrorAction SilentlyContinue "
    "-ErrorVariable lookupErrors);"
    "foreach($lookupError in $lookupErrors){"
    "if($lookupError.CategoryInfo.Category -ne 'ObjectNotFound'){throw $lookupError}};"
    "$found};"
)


@dataclass(frozen=True, slots=True)
class FirewallRule:
    name: str
    group: str
    remote_address: str
    address_type: str
    direction: str = "out"
    action: str = "block"
    profiles: str = "any"


class FirewallEnforcer(Protocol):
    def plan(
        self,
        session_id: str,
        blocked_ips: Iterable[str],
        blocked_cidrs: Iterable[str],
    ) -> tuple[FirewallRule, ...]: ...

    def reconcile(
        self,
        desired: tuple[FirewallRule, ...],
        current_rule_names: Iterable[str],
    ) -> None: ...

    def remove_rules(self, rule_names: Iterable[str]) -> None: ...

    def verify_rules(self, rules: tuple[FirewallRule, ...]) -> bool: ...


class WindowsFirewallEnforcer:
    """Manage exact EECP-owned, all-protocol rules through PowerShell."""

    def __init__(
        self,
        protected_endpoints: Iterable[str] = (),
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        platform: str = sys.platform,
    ):
        self._protected = tuple(ipaddress.ip_address(value) for value in protected_endpoints)
        self._runner = runner
        self._platform = platform

    def plan(
        self,
        session_id: str,
        blocked_ips: Iterable[str],
        blocked_cidrs: Iterable[str],
    ) -> tuple[FirewallRule, ...]:
        if not session_id.strip():
            raise ValueError("session_id is required for Firewall ownership")
        entries: list[tuple[str, str]] = []
        for value in blocked_ips:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError(f"invalid blocked IP address: {value}") from exc
            self._reject_management_overlap(address)
            entries.append(("IP", str(address)))
        for value in blocked_cidrs:
            try:
                network = ipaddress.ip_network(value, strict=True)
            except ValueError as exc:
                raise ValueError(f"invalid blocked CIDR network: {value}") from exc
            self._reject_management_overlap(network)
            entries.append(("CIDR", str(network)))

        session_key = _digest(session_id)
        group = f"EECP-{session_key}"
        return tuple(
            FirewallRule(
                name=f"{group}-{kind}-{_digest(remote)}",
                group=group,
                remote_address=remote,
                address_type=kind,
            )
            for kind, remote in sorted(set(entries))
        )

    def reconcile(
        self,
        desired: tuple[FirewallRule, ...],
        current_rule_names: Iterable[str],
    ) -> None:
        self._require_windows()
        current_names = set(current_rule_names)
        desired_names = {rule.name for rule in desired}
        self._validate_owned_names(current_names | desired_names)
        existing = self._query_rules(desired_names)
        replacements = []
        for rule in desired:
            if self._rule_matches(rule, existing.get(rule.name)):
                LOGGER.info(
                    "firewall operation=ensure rule=%s remote=%s result=unchanged",
                    rule.name,
                    rule.remote_address,
                )
                continue
            replacements.append({
                "name": rule.name,
                "display_name": rule.name,
                "group": rule.group,
                "remote_addresses": [
                    value.strip() for value in rule.remote_address.split(",") if value.strip()
                ],
                "direction": (
                    "Inbound"
                    if rule.direction.casefold() in {"in", "inbound"}
                    else "Outbound"
                ),
            })
        # Install replacements before removing old addresses: refreshing DNS
        # must not create a window where previously blocked flows can resume.
        obsolete = sorted(current_names - desired_names)
        if not replacements and not obsolete:
            return
        self._update_rules(replacements, obsolete)
        verified = self._query_rules(desired_names | set(obsolete))
        if any(name in verified for name in obsolete):
            raise OSError("Windows Firewall obsolete rule removal verification failed")
        if not all(self._rule_matches(rule, verified.get(rule.name)) for rule in desired):
            raise OSError("Windows Firewall rule verification failed")

    def remove_rules(self, rule_names: Iterable[str]) -> None:
        self._require_windows()
        names = sorted(set(rule_names))
        self._validate_owned_names(names)
        if names:
            self._update_rules([], names)
            if self._query_rules(names):
                raise OSError("Windows Firewall rule removal verification failed")

    def verify_rules(self, rules: tuple[FirewallRule, ...]) -> bool:
        self._require_windows()
        existing = self._query_rules(rule.name for rule in rules)
        return all(self._rule_matches(rule, existing.get(rule.name)) for rule in rules)

    def owned_rule_names(self) -> tuple[str, ...]:
        """Include rules left behind before a failed apply could persist state."""
        self._require_windows()
        existing = self._query_rules(("EECP-*",))
        return tuple(sorted(
            name for name, value in existing.items()
            if _is_owned_rule_name(name) and value.get("Group") == "-".join(name.split("-")[:2])
        ))

    @staticmethod
    def _validate_owned_names(names: Iterable[str]) -> None:
        for name in names:
            if not _is_owned_rule_name(name):
                raise ValueError(f"refusing to change non-EECP Firewall rule: {name}")

    def _update_rules(self, replacements: list[dict], obsolete: list[str]) -> None:
        script = _SCRIPT_HEADER + (
            "foreach($item in @($request.upsert)){"
            "$remoteAddresses=@($item.remote_addresses);"
            "if($remoteAddresses.Count -eq 0){throw 'RemoteAddress is required'};"
            "$existing=@(Get-EecpRule -Names @($item.name));"
            "if($existing.Count -gt 0){$existing|Remove-NetFirewallRule -ErrorAction Stop};"
            "New-NetFirewallRule -Name $item.name -DisplayName $item.display_name "
            "-Group $item.group -Direction $item.direction "
            "-Action Block -Profile Any -Enabled True -Protocol Any -LocalAddress Any "
            "-RemoteAddress $remoteAddresses -ErrorAction Stop|Out-Null};"
            "$obsolete=@(Get-EecpRule -Names @($request.remove));"
            "if($obsolete.Count -gt 0){$obsolete|Remove-NetFirewallRule -ErrorAction Stop}"
        )
        operation = "update" if replacements else "remove"
        self._run(script, {"upsert": replacements, "remove": obsolete}, operation=operation)

    def _reject_management_overlap(
        self,
        blocked: (
            ipaddress.IPv4Address
            | ipaddress.IPv6Address
            | ipaddress.IPv4Network
            | ipaddress.IPv6Network
        ),
    ) -> None:
        for protected in self._protected:
            if protected.version != blocked.version:
                continue
            if isinstance(blocked, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
                overlaps = protected == blocked
            else:
                overlaps = protected in blocked
            if overlaps:
                raise ValueError(
                    f"blocked address {blocked} overlaps protected Gateway endpoint {protected}"
                )

    def _query_rules(self, names: Iterable[str]) -> dict[str, dict]:
        names = sorted(set(names))
        if not names:
            return {}
        script = _SCRIPT_HEADER + (
            "$rows=@(foreach($rule in @(Get-EecpRule -Names @($request.names))){"
            "$filter=$rule|Get-NetFirewallAddressFilter -ErrorAction Stop;"
            "$port=$rule|Get-NetFirewallPortFilter -ErrorAction Stop;"
            "[PSCustomObject]@{Name=$rule.Name;DisplayName=$rule.DisplayName;"
            "Group=$rule.Group;"
            "Enabled=$rule.Enabled.ToString();"
            "Direction=$rule.Direction.ToString();Action=$rule.Action.ToString();"
            "Profile=$rule.Profile.ToString();Protocol=$port.Protocol.ToString();"
            "LocalAddress=@($filter.LocalAddress);"
            "RemoteAddress=@($filter.RemoteAddress)}});"
            "ConvertTo-Json -InputObject @($rows) -Depth 6 -Compress"
        )
        result = self._run(script, {"names": names}, operation="query")
        try:
            values = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OSError("Windows Firewall query returned invalid JSON") from exc
        if not isinstance(values, list) or any(
            not isinstance(value, dict) or not isinstance(value.get("Name"), str)
            for value in values
        ):
            raise OSError("Windows Firewall query returned invalid rule records")
        return {value["Name"]: value for value in values}

    @staticmethod
    def _rule_matches(rule: FirewallRule, value: dict | None) -> bool:
        if not isinstance(value, dict):
            return False
        remote = value.get("RemoteAddress", []) if isinstance(value, dict) else []
        if isinstance(remote, str):
            remote = [remote]
        local = value.get("LocalAddress", []) if isinstance(value, dict) else []
        if isinstance(local, str):
            local = [local]
        direction = str(value.get("Direction", "")).casefold()
        expected_dir = rule.direction.casefold()
        direction_matches = (
            direction == expected_dir
            or (expected_dir in {"out", "outbound"} and direction in {"out", "outbound"})
            or (expected_dir in {"in", "inbound"} and direction in {"in", "inbound"})
        )
        profile = str(value.get("Profile", "")).casefold()
        expected_profile = rule.profiles.casefold()
        profile_matches = (
            profile == expected_profile
            or (
                expected_profile == "any"
                and (
                    profile == "any"
                    or ("public" in profile and "private" in profile and "domain" in profile)
                )
            )
        )
        return bool(
            isinstance(value, dict)
            and value.get("Name") == rule.name
            and value.get("DisplayName") == rule.name
            and value.get("Group") == rule.group
            and str(value.get("Enabled", "")).casefold() == "true"
            and direction_matches
            and str(value.get("Action", "")).casefold() == rule.action.casefold()
            and profile_matches
            and str(value.get("Protocol", "")).casefold() == "any"
            and {str(item).casefold() for item in local} == {"any"}
            and _same_remote_addresses(
                ",".join(str(item) for item in remote), rule.remote_address
            )
        )

    def _run(
        self,
        script: str,
        payload: dict,
        *,
        operation: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._runner(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
                timeout=FIREWALL_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise OSError(
                f"Windows Firewall {operation} timed out after "
                f"{FIREWALL_COMMAND_TIMEOUT_SECONDS:g}s"
            ) from exc
        if result.returncode != 0:
            detail = _command_error(result)
            suffix = f": {detail}" if detail else ""
            raise OSError(f"Windows Firewall {operation} failed (exit {result.returncode}){suffix}")
        LOGGER.info("firewall operation=%s result=succeeded", operation)
        return result

    def _require_windows(self) -> None:
        if self._platform != "win32":
            raise OSError("Windows Firewall enforcement is supported only on Windows")


def resolve_gateway_addresses(
    gateway_url: str,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Resolve the trusted local Gateway configuration for self-lockout checks."""

    hostname = urlparse(gateway_url).hostname
    if not hostname:
        raise ValueError("configured Gateway URL has no hostname")
    try:
        return (str(ipaddress.ip_address(hostname)),)
    except ValueError:
        try:
            records = resolver(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise OSError(f"cannot resolve configured Gateway endpoint: {hostname}") from exc
        addresses = sorted({str(ipaddress.ip_address(record[4][0])) for record in records})
        if not addresses:
            raise OSError(f"configured Gateway endpoint has no IP address: {hostname}") from None
        return tuple(addresses)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    """Return bounded single-line diagnostic text without echoing command input."""

    return " ".join((result.stderr or "").split())[:500]


def _is_owned_rule_name(name: str) -> bool:
    parts = name.split("-")
    if len(parts) == 5 and parts[-1] == "IN":
        parts = parts[:-1]
    return (
        len(parts) == 4
        and parts[0] == "EECP"
        and len(parts[1]) == 12
        and parts[2] in {"IP", "CIDR", "DOMAIN"}
        and len(parts[3]) == 12
        and all(character in "0123456789ABCDEF" for character in parts[1] + parts[3])
    )


def domain_traffic_rules(validated_ip_rules: tuple[FirewallRule, ...]) -> tuple[FirewallRule, ...]:
    """Group validated IPs into one rule per direction, covering TCP and UDP.

    Incoming blocks also stop an already-running response/one-way stream. The
    caller must use plan() first so protected Gateway addresses are rejected.
    """
    if not validated_ip_rules:
        return ()
    remote = ",".join(sorted({rule.remote_address for rule in validated_ip_rules}))
    first = validated_ip_rules[0]
    outbound = replace(
        first,
        name=f"{first.group}-DOMAIN-{_digest(remote)}",
        remote_address=remote,
        address_type="DOMAIN",
    )
    return (outbound, replace(outbound, name=f"{outbound.name}-IN", direction="in"))


def _same_remote_addresses(actual: str, expected: str) -> bool:
    def normalized(value: str) -> set[str]:
        networks = [ipaddress.ip_network(item.strip(), strict=False) for item in value.split(",")]
        return {
            str(network)
            for version in (4, 6)
            for network in ipaddress.collapse_addresses(
                item for item in networks if item.version == version
            )
        }

    try:
        return normalized(actual) == normalized(expected)
    except ValueError:
        return False
