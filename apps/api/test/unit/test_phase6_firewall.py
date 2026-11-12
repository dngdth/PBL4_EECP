from __future__ import annotations

import json
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

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

    def owned_rule_names(self):
        return tuple(self.rules)


class WindowsFirewallRunner:
    """Stateful test double for exact PowerShell Firewall operations."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.rules: dict[str, dict] = {}

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if arguments[0] == "powershell.exe":
            script = arguments[4]
            payload = json.loads(kwargs["input"])
            if "New-NetFirewallRule" in script:
                for item in payload["upsert"]:
                    name = item["name"]
                    self.rules[name] = {
                        "Name": name,
                        "DisplayName": item["display_name"],
                        "Group": item["group"],
                        "Enabled": True,
                        "Direction": item["direction"],
                        "Action": "Block",
                        "Profile": "Any",
                        "Protocol": "Any",
                        "LocalAddress": ["Any"],
                        "RemoteAddress": item["remote_addresses"],
                    }
                for name in payload["remove"]:
                    self.rules.pop(name, None)
                return subprocess.CompletedProcess(arguments, 0, "", "")
            rules = [
                value for name, value in self.rules.items()
                if any(fnmatchcase(name, pattern) for pattern in payload["names"])
            ]
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=json.dumps(rules),
                stderr="",
            )

        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")


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
            domain_resolver=SimpleNamespace(
                resolve=lambda domains: {domain: ("203.0.113.20",) for domain in domains}
            ),
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


def test_windows_adapter_uses_exact_name_and_argument_safe_powershell() -> None:
    runner = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    planned = adapter.plan("SES-001", ["203.0.113.10"], [])
    adapter.reconcile(planned, ())

    add_arguments, add_kwargs = next(
        item for item in runner.calls if "New-NetFirewallRule" in item[0][4]
    )
    assert add_arguments[:4] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]
    assert "ConvertFrom-Json ([Console]::In.ReadToEnd())" in add_arguments[4]
    assert add_arguments[5:] == []
    assert json.loads(add_kwargs["input"]) == {
        "upsert": [{
        "name": planned[0].name,
        "display_name": planned[0].name,
        "group": planned[0].group,
        "remote_addresses": ["203.0.113.10"],
        "direction": "Outbound",
        }],
        "remove": [],
    }
    assert "shell" not in add_kwargs
    assert adapter.verify_rules(planned)


def test_windows_adapter_sends_mixed_multi_address_rule_as_json_array() -> None:
    runner = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    planned = adapter.plan(
        "SES-001",
        ["104.18.32.47", "172.64.155.209", "2a06:98c1:3101::6812:202f"],
        [],
    )
    remote = ",".join(rule.remote_address for rule in planned)
    grouped = (
        FirewallRule(
            name="EECP-037F6D701104-DOMAIN-49A3D787EBF0",
            group="EECP-037F6D701104",
            remote_address=remote,
            address_type="DOMAIN",
        ),
    )

    adapter.reconcile(grouped, ())

    add_arguments, add_kwargs = next(
        item for item in runner.calls if "New-NetFirewallRule" in item[0][4]
    )
    payload = json.loads(add_kwargs["input"])["upsert"][0]
    assert add_arguments[5:] == []
    assert payload["remote_addresses"] == [
        "104.18.32.47",
        "172.64.155.209",
        "2a06:98c1:3101::6812:202f",
    ]
    assert adapter.verify_rules(grouped)


def test_firewall_batches_many_rules_and_reuses_matching_rules():
    runner = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    rules = adapter.plan("SES-BATCH", [f"203.0.113.{value}" for value in range(1, 21)], [])
    runner.rules["School-Allow-DNS"] = {"external": True}

    adapter.reconcile(rules, ())
    assert len(runner.calls) == 3  # Snapshot, update, verification for all 20 rules.
    assert all(options["timeout"] == 30.0 for _, options in runner.calls)
    runner.calls.clear()
    adapter.reconcile(rules, (rule.name for rule in rules))
    assert len(runner.calls) == 1
    runner.calls.clear()
    adapter.remove_rules(rule.name for rule in rules)
    assert len(runner.calls) == 2
    assert runner.rules == {"School-Allow-DNS": {"external": True}}


@pytest.mark.parametrize("operation", ["query", "update"])
def test_firewall_timeout_reports_operation_and_allows_slow_provider(operation):
    memory = WindowsFirewallRunner()

    def runner(arguments, **options):
        # A provider that takes six seconds must fit the configured budget.
        assert options["timeout"] >= 6
        current = "update" if "New-NetFirewallRule" in arguments[4] else "query"
        if current == operation:
            raise subprocess.TimeoutExpired(arguments, options["timeout"])
        return memory(arguments, **options)

    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    rules = adapter.plan("SES-TIMEOUT", ["203.0.113.10"], [])
    with pytest.raises(OSError, match=f"Windows Firewall {operation} timed out after 30s"):
        adapter.reconcile(rules, ())


def test_firewall_query_failure_does_not_attempt_mutation():
    calls = []

    def runner(arguments, **options):
        calls.append((arguments, options))
        return subprocess.CompletedProcess(arguments, 1, "", "Access is denied")

    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    rules = adapter.plan("SES-ERROR", ["203.0.113.10"], [])
    with pytest.raises(OSError, match="Windows Firewall query failed.*Access is denied"):
        adapter.reconcile(rules, ())
    assert len(calls) == 1
    assert "New-NetFirewallRule" not in calls[0][0][4]


def test_firewall_removal_requires_confirmation_and_validates_all_names_first():
    memory = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=memory, platform="win32")
    rules = adapter.plan("SES-REMOVE", ["203.0.113.10"], [])
    adapter.reconcile(rules, ())
    memory.calls.clear()
    with pytest.raises(ValueError, match="non-EECP"):
        adapter.remove_rules([rules[0].name, "School-Allow-DNS"])
    assert memory.calls == []

    def ignore_removal(arguments, **options):
        if "New-NetFirewallRule" in arguments[4]:
            return subprocess.CompletedProcess(arguments, 0, "", "")
        return memory(arguments, **options)

    adapter = WindowsFirewallEnforcer(runner=ignore_removal, platform="win32")
    with pytest.raises(OSError, match="removal verification failed"):
        adapter.remove_rules([rules[0].name])


def test_firewall_reconcile_detects_obsolete_rules_that_remain_installed():
    memory = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=memory, platform="win32")
    previous = adapter.plan("SES-UPDATE", ["203.0.113.10"], [])
    desired = adapter.plan("SES-UPDATE", ["203.0.113.11"], [])
    adapter.reconcile(previous, ())

    def ignore_old_removal(arguments, **options):
        payload = json.loads(options["input"])
        if "upsert" in payload:
            payload["remove"] = []
            options["input"] = json.dumps(payload)
        return memory(arguments, **options)

    adapter = WindowsFirewallEnforcer(runner=ignore_old_removal, platform="win32")
    with pytest.raises(OSError, match="obsolete rule removal verification failed"):
        adapter.reconcile(desired, (rule.name for rule in previous))


def test_firewall_inventory_requires_both_owned_name_and_matching_group():
    runner = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    owned = adapter.plan("OLD-SESSION", ["203.0.113.10"], [])
    adapter.reconcile(owned, ())
    foreign = adapter.plan("FOREIGN-SESSION", ["203.0.113.11"], [])[0]
    runner.rules[foreign.name] = {"Name": foreign.name, "Group": "School-Managed"}
    runner.rules["EECP-Custom-Admin"] = {"Name": "EECP-Custom-Admin", "Group": "EECP-Admin"}
    runner.rules["School-Allow-DNS"] = {"Name": "School-Allow-DNS", "Group": "School"}

    assert adapter.owned_rule_names() == (owned[0].name,)
    adapter.remove_rules(adapter.owned_rule_names())
    assert set(runner.rules) == {foreign.name, "EECP-Custom-Admin", "School-Allow-DNS"}


@pytest.mark.parametrize("with_active_state", [False, True])
def test_restore_removes_orphaned_rules_from_old_failed_policy(tmp_path, with_active_state):
    firewall = MemoryFirewall()
    enforcer, _hosts, _calls = _enforcer(tmp_path, firewall)
    old_rule = firewall.plan("OLD-FAILED-SESSION", ["203.0.113.99"], [])[0]
    firewall.rules[old_rule.name] = old_rule
    if with_active_state:
        enforcer.apply(_payload(ips=["203.0.113.10"]))
    assert old_rule.name in firewall.rules

    enforcer.restore()
    assert firewall.rules == {}
    assert firewall.external == {"School-Allow-DNS", "Admin-Rule-A"}
    assert not (tmp_path / "state.json").exists()
    enforcer.restore()
    assert firewall.rules == {}


def test_restore_inventory_failure_preserves_state_for_retry(tmp_path):
    firewall = MemoryFirewall()
    enforcer, _hosts, _calls = _enforcer(tmp_path, firewall)
    enforcer.apply(_payload(ips=["203.0.113.10"]))
    original = (tmp_path / "state.json").read_bytes()

    def fail_inventory():
        raise OSError("cannot query owned Firewall rules")

    firewall.owned_rule_names = fail_inventory
    with pytest.raises(OSError, match="cannot query owned Firewall rules"):
        enforcer.restore()
    assert (tmp_path / "state.json").read_bytes() == original
    assert firewall.rules


def test_windows_adapter_surfaces_bounded_firewall_creation_error() -> None:
    def runner(arguments, **_kwargs):
        script = arguments[4]
        if "New-NetFirewallRule" in script:
            return subprocess.CompletedProcess(
                arguments,
                1,
                "",
                "New-NetFirewallRule: The address is invalid.\n" + "x" * 600,
            )
        return subprocess.CompletedProcess(arguments, 0, "[]", "")

    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    planned = adapter.plan("SES-001", ["203.0.113.10"], [])

    with pytest.raises(OSError) as caught:
        adapter.reconcile(planned, ())

    message = str(caught.value)
    assert "The address is invalid" in message
    prefix = "Windows Firewall update failed (exit 1): "
    assert len(message.removeprefix(prefix)) == 500


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("Group", "Other-Owner"),
        ("DisplayName", "Duplicate friendly name"),
        ("Enabled", False),
        ("Direction", "in"),
        ("Action", "allow"),
        ("Profile", "private"),
        ("Protocol", "TCP"),
        ("LocalAddress", ["192.0.2.10"]),
        ("RemoteAddress", ["198.51.100.99"]),
    ],
)
def test_windows_adapter_detects_and_repairs_tampered_owned_rule(
    field: str, tampered_value: object
) -> None:
    runner = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    planned = adapter.plan("SES-001", ["203.0.113.10"], [])
    adapter.reconcile(planned, ())
    rule = planned[0]
    runner.rules["School-Allow-DNS"] = {"external": True}

    runner.rules[rule.name][field] = tampered_value

    assert not adapter.verify_rules(planned)
    adapter.reconcile(planned, (rule.name,))
    assert adapter.verify_rules(planned)
    assert runner.rules["School-Allow-DNS"] == {"external": True}


def test_duplicate_display_name_cannot_replace_unique_managed_name() -> None:
    runner = WindowsFirewallRunner()
    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    planned = adapter.plan("SES-001", ["203.0.113.10"], [])
    adapter.reconcile(planned, ())
    managed = planned[0]
    runner.rules["EXTERNAL-RULE-ID"] = {
        "Name": "EXTERNAL-RULE-ID",
        "DisplayName": managed.name,
        "Group": managed.group,
        "Enabled": True,
        "Direction": "Outbound",
        "Action": "Block",
        "Profile": "Any",
        "Protocol": "Any",
        "LocalAddress": ["Any"],
        "RemoteAddress": [managed.remote_address],
    }

    assert adapter.verify_rules(planned)
    adapter.reconcile(planned, (managed.name,))
    assert "EXTERNAL-RULE-ID" in runner.rules


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
