from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent.infrastructure.policy_enforcement import AuditPolicyEnforcer, WindowsPolicyEnforcer
from agent.infrastructure.windows.firewall_enforcer import (
    FirewallRule,
    WindowsFirewallEnforcer,
    resolve_gateway_addresses,
)
from contracts.v2 import PolicyRules, compute_policy_hash


def _payload(
    *,
    ips: list[str] | None = None,
    cidrs: list[str] | None = None,
    domains: list[str] | None = None,
) -> dict:
    network = {
        "blocked_domains": domains or [],
        "blocked_ips": ips or [],
        "blocked_cidrs": cidrs or [],
    }
    rules = {"network": network}
    return {
        "format": "eecp-policy/v1",
        "profile": "NETWORK_TEST",
        "version": 1,
        "session_id": "SES-001",
        "policy_hash": compute_policy_hash(
            "NETWORK_TEST", 1, PolicyRules.model_validate(rules)
        ),
        "rules": rules,
    }


class MemoryFirewall:
    def __init__(self, protected: tuple[str, ...] = ()):
        self._planner = WindowsFirewallEnforcer(protected, platform="win32")
        self.rules: dict[str, FirewallRule] = {}
        self.external = {"School-Allow-DNS", "Admin-Rule-A"}
        self.fail_next = False
        self.reconciliations = 0

    def plan(self, session_id, blocked_ips, blocked_cidrs):
        return self._planner.plan(session_id, blocked_ips, blocked_cidrs)

    def reconcile(self, desired, current_rule_names):
        self.reconciliations += 1
        desired_names = {rule.name for rule in desired}
        for name in set(current_rule_names) - desired_names:
            self.rules.pop(name, None)
        for rule in desired:
            self.rules[rule.name] = rule
        if self.fail_next:
            self.fail_next = False
            raise OSError("simulated Firewall failure")

    def remove_rules(self, rule_names):
        for name in rule_names:
            self.rules.pop(name, None)

    def verify_rules(self, rules):
        return all(self.rules.get(rule.name) == rule for rule in rules)


def _enforcer(tmp_path: Path, firewall: MemoryFirewall):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    return (
        WindowsPolicyEnforcer(
            tmp_path / "state.json",
            hosts_path=hosts,
            runner=runner,
            firewall=firewall,
        ),
        hosts,
        calls,
    )


def test_firewall_plans_direct_ip_and_cidr_as_deterministic_outbound_blocks() -> None:
    adapter = WindowsFirewallEnforcer(platform="win32")

    first = adapter.plan("SES-001", ["203.0.113.10", "2001:db8::10"], ["198.51.100.0/24"])
    second = adapter.plan("SES-001", ["203.0.113.10", "2001:db8::10"], ["198.51.100.0/24"])

    assert first == second
    assert {rule.remote_address for rule in first} == {
        "203.0.113.10",
        "2001:db8::10",
        "198.51.100.0/24",
    }
    assert all(rule.name.startswith("EECP-") for rule in first)
    assert all(rule.direction == "out" and rule.action == "block" for rule in first)
    assert all(rule.profiles == "any" for rule in first)


@pytest.mark.parametrize(
    ("ips", "cidrs"),
    [
        (["999.1.1.1"], []),
        (["not-an-ip"], []),
        (["203.0.113.10; powershell evil"], []),
        ([], ["10.0.0.1/500"]),
        ([], ["198.51.100.1/24"]),
    ],
)
def test_invalid_or_injectable_addresses_are_rejected_before_runner(ips, cidrs) -> None:
    calls = []
    adapter = WindowsFirewallEnforcer(
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        platform="win32",
    )

    with pytest.raises(ValueError):
        adapter.plan("SES-001", ips, cidrs)

    assert calls == []


def test_management_endpoint_ip_or_containing_cidr_is_rejected() -> None:
    adapter = WindowsFirewallEnforcer(("192.168.1.10",), platform="win32")

    with pytest.raises(ValueError, match="protected Gateway"):
        adapter.plan("SES-001", ["192.168.1.10"], [])
    with pytest.raises(ValueError, match="protected Gateway"):
        adapter.plan("SES-001", [], ["192.168.1.0/24"])


def test_gateway_address_resolution_uses_trusted_config_and_supports_ipv6() -> None:
    assert resolve_gateway_addresses("wss://[2001:db8::10]:8443/ws") == ("2001:db8::10",)
    assert resolve_gateway_addresses(
        "wss://gateway.school/ws",
        resolver=lambda *_args, **_kwargs: [
            (None, None, None, None, ("192.0.2.10", 0)),
            (None, None, None, None, ("192.0.2.10", 0)),
        ],
    ) == ("192.0.2.10",)


def test_windows_adapter_builds_argument_list_without_shell_or_raw_command() -> None:
    calls = []
    installed: set[str] = set()

    def runner(arguments, **kwargs):
        calls.append((arguments, kwargs))
        name = next((item[5:] for item in arguments if item.startswith("name=")), "")
        if "show" in arguments:
            if name not in installed:
                return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="")
            rule = planned[0]
            output = f"{rule.name} Outbound Block {rule.remote_address}"
            return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")
        if "add" in arguments:
            installed.add(name)
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    planned = adapter.plan("SES-001", ["203.0.113.10"], [])
    adapter.reconcile(planned, ())

    add_arguments, add_kwargs = next(item for item in calls if "add" in item[0])
    assert add_arguments[0] == "netsh"
    assert "dir=out" in add_arguments
    assert "action=block" in add_arguments
    assert "remoteip=203.0.113.10" in add_arguments
    assert "shell" not in add_kwargs


def test_adapter_refuses_non_eecp_rule_removal_and_non_windows_invocation() -> None:
    windows = WindowsFirewallEnforcer(platform="win32")
    with pytest.raises(ValueError, match="non-EECP"):
        windows.remove_rules(["School-Allow-DNS"])

    non_windows = WindowsFirewallEnforcer(platform="linux")
    planned = non_windows.plan("SES-001", ["203.0.113.10"], [])
    with pytest.raises(OSError, match="only on Windows"):
        non_windows.reconcile(planned, ())


def test_apply_restore_update_and_maintenance_are_idempotent_and_preserve_external_rules(
    tmp_path: Path,
) -> None:
    firewall = MemoryFirewall()
    enforcer, _hosts, _calls = _enforcer(tmp_path, firewall)
    policy_a = _payload(ips=["203.0.113.10", "203.0.113.11"])
    policy_b = _payload(ips=["203.0.113.11", "203.0.113.12"])

    enforcer.apply(policy_a)
    names_after_first = set(firewall.rules)
    enforcer.apply(policy_a)
    assert set(firewall.rules) == names_after_first

    enforcer.apply(policy_b)
    assert {rule.remote_address for rule in firewall.rules.values()} == {
        "203.0.113.11",
        "203.0.113.12",
    }
    removed_name = next(iter(firewall.rules))
    firewall.rules.pop(removed_name)
    enforcer.maintain()
    assert len(firewall.rules) == 2

    enforcer.restore()
    enforcer.restore()
    assert firewall.rules == {}
    assert firewall.external == {"School-Allow-DNS", "Admin-Rule-A"}


def test_firewall_failure_rolls_back_hosts_partial_rules_and_hash_state(tmp_path: Path) -> None:
    firewall = MemoryFirewall()
    firewall.fail_next = True
    enforcer, hosts, _calls = _enforcer(tmp_path, firewall)
    original_hosts = hosts.read_text(encoding="utf-8")

    with pytest.raises(OSError, match="simulated Firewall failure"):
        enforcer.apply(
            _payload(domains=["blocked.example"], ips=["203.0.113.10"])
        )

    assert hosts.read_text(encoding="utf-8") == original_hosts
    assert firewall.rules == {}
    assert not (tmp_path / "state.json").exists()


def test_audit_mode_records_intent_without_firewall_mutation(tmp_path: Path) -> None:
    state_path = tmp_path / "audit-state.json"
    payload = _payload(ips=["203.0.113.10"], cidrs=["198.51.100.0/24"])

    AuditPolicyEnforcer(state_path).apply(payload)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["mode"] == "audit"
    assert state["blocked_ips"] == ["203.0.113.10"]
    assert state["blocked_cidrs"] == ["198.51.100.0/24"]
