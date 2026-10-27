from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlparse

RULE_PREFIX = "EECP-"
LOGGER = logging.getLogger(__name__)


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
    """Manage exact EECP-owned, all-protocol block rules through netsh."""

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
        desired_names = {rule.name for rule in desired}
        for rule in desired:
            if self._rule_matches(rule):
                LOGGER.info(
                    "firewall operation=ensure rule=%s remote=%s result=unchanged",
                    rule.name,
                    rule.remote_address,
                )
                continue
            self._remove_rule(rule.name)
            result = self._run(
                [
                    "netsh",
                    "advfirewall",
                    "firewall",
                    "add",
                    "rule",
                    f"name={rule.name}",
                    f"group={rule.group}",
                    f"dir={rule.direction}",
                    "action=block",
                    f"remoteip={rule.remote_address}",
                    "profile=any",
                    "protocol=any",
                    "enable=yes",
                ]
            )
            if result.returncode != 0:
                raise OSError(f"cannot create Windows Firewall rule {rule.name}")
            LOGGER.info(
                "firewall operation=add rule=%s remote=%s result=succeeded",
                rule.name,
                rule.remote_address,
            )
        # Install replacements before removing old addresses: refreshing DNS
        # must not create a window where previously blocked flows can resume.
        for name in sorted(set(current_rule_names) - desired_names):
            LOGGER.info("firewall operation=remove rule=%s result=started", name)
            self._remove_rule(name)
        if not self.verify_rules(desired):
            raise OSError("Windows Firewall rule verification failed")

    def remove_rules(self, rule_names: Iterable[str]) -> None:
        self._require_windows()
        for name in sorted(set(rule_names)):
            LOGGER.info("firewall operation=remove rule=%s result=started", name)
            self._remove_rule(name)

    def verify_rules(self, rules: tuple[FirewallRule, ...]) -> bool:
        self._require_windows()
        return all(self._rule_matches(rule) for rule in rules)

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

    def _rule_matches(self, rule: FirewallRule) -> bool:
        result = self._run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "show",
                "rule",
                f"name={rule.name}",
                "verbose",
            ]
        )
        if result.returncode != 0:
            return False
        fields = {
            match[1].strip().casefold(): match[2].strip().casefold()
            for line in result.stdout.splitlines()
            if (match := re.match(r"^\s*([A-Za-z][A-Za-z ]*):\s*(.*?)\s*$", line))
        }
        direction = {"out", "outbound"} if rule.direction == "out" else {"in", "inbound"}
        profiles = {value.strip() for value in fields.get("profiles", "").split(",")}
        return (
            fields.get("rule name") == rule.name.casefold()
            and fields.get("direction") in direction
            and fields.get("action") == "block"
            and fields.get("enabled") == "yes"
            and fields.get("protocol") == "any"
            and fields.get("localip") == "any"
            and profiles in ({"any"}, {"domain", "private", "public"})
            and _same_remote_addresses(fields.get("remoteip", ""), rule.remote_address)
        )

    def _remove_rule(self, name: str) -> None:
        if not _is_owned_rule_name(name):
            raise ValueError(f"refusing to remove non-EECP Firewall rule: {name}")
        existing = self._run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"])
        if existing.returncode != 0:
            return
        result = self._run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"])
        if result.returncode != 0:
            raise OSError(f"cannot remove Windows Firewall rule {name}")

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(arguments, capture_output=True, text=True, check=False, timeout=5.0)
        except subprocess.TimeoutExpired as exc:
            raise OSError("Windows Firewall command timed out") from exc

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
