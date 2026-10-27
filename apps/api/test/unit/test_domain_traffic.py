from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.infrastructure.policy_enforcement import WindowsPolicyEnforcer
from agent.infrastructure.windows.domain_resolver import WindowsDomainResolver
from agent.infrastructure.windows.firewall_enforcer import (
    WindowsFirewallEnforcer,
    domain_traffic_rules,
)
from contracts.v2 import PolicyRules, compute_policy_hash


class Resolver:
    def __init__(self):
        self.answers = {"chatgpt.com": ("203.0.113.10", "2001:db8::10")}
        self.calls = 0
        self.fail = False

    def resolve(self, domains):
        self.calls += 1
        if self.fail:
            raise OSError("DNS unavailable")
        return {domain: self.answers.get(domain, ()) for domain in domains}


class Firewall:
    def __init__(self, protected=()):
        self.planner = WindowsFirewallEnforcer(protected, platform="win32")
        self.rules = {}
        self.fail = False

    def plan(self, *args):
        return self.planner.plan(*args)

    def reconcile(self, desired, previous):
        self.rules.update({rule.name: rule for rule in desired})
        if self.fail:
            self.fail = False
            raise OSError("partial firewall failure")
        for name in set(previous) - {rule.name for rule in desired}:
            self.rules.pop(name, None)

    def remove_rules(self, names):
        for name in names:
            self.rules.pop(name, None)


def payload(domains=("chatgpt.com",), session="SES-TRAFFIC"):
    rules = {"network": {"blocked_domains": list(domains)}}
    return {
        "format": "eecp-policy/v1",
        "profile": "TRAFFIC",
        "version": 1,
        "session_id": session,
        "policy_hash": compute_policy_hash("TRAFFIC", 1, PolicyRules.model_validate(rules)),
        "rules": rules,
    }


def make_enforcer(tmp_path, *, firewall=None, resolver=None, clock=lambda: 0.0):
    hosts = tmp_path / "hosts"
    if not hosts.exists():
        hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    return WindowsPolicyEnforcer(
        tmp_path / "policy.json",
        hosts_path=hosts,
        runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        firewall=firewall or Firewall(),
        domain_resolver=resolver or Resolver(),
        clock=clock,
    )


def test_resolver_matches_cached_subdomains_and_filters_sinkholes():
    calls = []
    records = [
        {"name": "api.chatgpt.com.", "address": "203.0.113.9"},
        {"name": "chatgpt.com", "address": "2001:db8::10"},
        {"name": "www.chatgpt.com", "address": "127.0.0.1"},
        {"name": "chatgpt.com", "address": "::1"},
        {"name": "chatgpt.com", "address": "0.0.0.0"},
        {"name": "chatgpt.com", "address": "ff02::1"},
        {"name": "chatgpt.com", "address": "fe80::1"},
        {"name": "chatgpt.com.attacker.org", "address": "203.0.113.99"},
    ]

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(records), "")

    resolver = WindowsDomainResolver(runner=runner)
    assert resolver.resolve(["chatgpt.com"]) == {"chatgpt.com": ("2001:db8::10", "203.0.113.9")}
    args, kwargs = calls[0]
    assert json.loads(kwargs["input"]) == ["chatgpt.com"]
    assert "-NoHostsFile" in args[-1]
    assert "Get-DnsClientCache" in args[-1]
    assert "shell" not in kwargs
    assert kwargs["timeout"] == 8.0


@pytest.mark.parametrize("output", ["", "{}", "null", '[{"name":"chatgpt.com"}]'])
def test_resolver_rejects_malformed_discovery(output):
    resolver = WindowsDomainResolver(
        runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output, "")
    )
    with pytest.raises(OSError):
        resolver.resolve(["chatgpt.com"])


def test_resolver_is_bounded_and_validates_domains_before_execution():
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    resolver = WindowsDomainResolver(runner=timeout)
    with pytest.raises(ValueError):
        resolver.resolve(["chatgpt.com;whoami"])
    with pytest.raises(OSError, match="timed out"):
        resolver.resolve(["chatgpt.com"])
    assert resolver.resolve([]) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell JSON compatibility")
@pytest.mark.parametrize("domains", [("chatgpt.com",), ("chatgpt.com", "claude.ai")])
def test_powershell_handles_single_and_multiple_domains_without_real_dns(domains):
    # Execute the real PowerShell parsing/loop logic but replace both DNS
    # cmdlets, so this regression test needs neither network nor elevation.
    def runner(args, **kwargs):
        args = list(args)
        args[-1] = (
            "function Get-DnsClientCache {\n"
            " [pscustomobject]@{Entry='api.chatgpt.com'; RecordType=1; Data='203.0.113.11'}\n"
            " [pscustomobject]@{Entry='chatgpt.com'; RecordType=1; Data=''}\n"
            "}\n"
            "function Resolve-DnsName { [pscustomobject]@{IPAddress='203.0.113.10'} }\n" + args[-1]
        )
        return subprocess.run(args, **kwargs)

    resolved = WindowsDomainResolver(runner=runner).resolve(domains)
    expected = {domain: ("203.0.113.10",) for domain in domains}
    expected["chatgpt.com"] = ("203.0.113.10", "203.0.113.11")
    assert resolved == expected


def test_apply_discovers_before_hosts_and_installs_both_directions(tmp_path: Path):
    firewall = Firewall()

    class BeforeHostsResolver(Resolver):
        def resolve(self, domains):
            assert "chatgpt.com" not in (tmp_path / "hosts").read_text()
            return super().resolve(domains)

    enforcer = make_enforcer(tmp_path, firewall=firewall, resolver=BeforeHostsResolver())
    policy = payload()
    assert enforcer.apply(policy) == policy["policy_hash"]
    state = json.loads((tmp_path / "policy.json").read_text())
    assert state["domain_addresses"] == {"chatgpt.com": ["2001:db8::10", "203.0.113.10"]}
    assert {rule.direction for rule in firewall.rules.values()} == {"in", "out"}
    assert all(
        rule.remote_address == "2001:db8::10,203.0.113.10" for rule in firewall.rules.values()
    )
    assert "127.0.0.1 chatgpt.com" in (tmp_path / "hosts").read_text()
    assert state["policy_hash"] == policy["policy_hash"]
    enforcer.restore()
    enforcer.restore()
    assert not firewall.rules
    assert not (tmp_path / "policy.json").exists()


def test_refresh_retains_old_connection_addresses_and_recovers_deleted_rule(tmp_path: Path):
    now = [0.0]
    firewall, resolver = Firewall(), Resolver()
    enforcer = make_enforcer(tmp_path, firewall=firewall, resolver=resolver, clock=lambda: now[0])
    enforcer.apply(payload())
    resolver.answers = {"chatgpt.com": ("203.0.113.11",)}
    firewall.rules.clear()
    enforcer.maintain()
    assert len(firewall.rules) == 2
    assert resolver.calls == 1  # Maintenance repairs immediately; DNS is throttled.
    now[0] = 61.0
    enforcer.maintain()
    addresses = {rule.remote_address for rule in firewall.rules.values()}
    assert addresses == {"2001:db8::10,203.0.113.10,203.0.113.11"}
    assert resolver.calls == 2
    # A new Service instance reconstructs both directions from persisted state.
    restarted = make_enforcer(tmp_path, firewall=firewall, resolver=resolver)
    firewall.rules.clear()
    restarted.maintain()
    assert {rule.remote_address for rule in firewall.rules.values()} == addresses


def test_refresh_failure_keeps_active_rules_and_state(tmp_path: Path):
    now = [0.0]
    firewall, resolver = Firewall(), Resolver()
    enforcer = make_enforcer(tmp_path, firewall=firewall, resolver=resolver, clock=lambda: now[0])
    enforcer.apply(payload())
    original_state = (tmp_path / "policy.json").read_bytes()
    original_rules = dict(firewall.rules)
    resolver.fail = True
    now[0] = 61.0
    with pytest.raises(OSError, match="DNS unavailable"):
        enforcer.maintain()
    assert firewall.rules == original_rules
    assert (tmp_path / "policy.json").read_bytes() == original_state


def test_partial_refresh_rolls_back_and_policy_change_drops_old_domains(tmp_path: Path):
    now = [0.0]
    firewall, resolver = Firewall(), Resolver()
    enforcer = make_enforcer(tmp_path, firewall=firewall, resolver=resolver, clock=lambda: now[0])
    enforcer.apply(payload())
    original_rules = dict(firewall.rules)
    original_state = (tmp_path / "policy.json").read_bytes()
    resolver.answers = {"chatgpt.com": ("203.0.113.11",)}
    firewall.fail = True
    now[0] = 61.0
    with pytest.raises(OSError, match="partial firewall"):
        enforcer.maintain()
    assert firewall.rules == original_rules
    assert (tmp_path / "policy.json").read_bytes() == original_state
    resolver.answers = {"claude.ai": ("203.0.113.12",)}
    enforcer.apply(payload(["claude.ai"]))
    assert {rule.remote_address for rule in firewall.rules.values()} == {"203.0.113.12"}
    assert "chatgpt.com" not in (tmp_path / "hosts").read_text()


@pytest.mark.parametrize("answers", [(), ("127.0.0.1", "::1")])
def test_missing_real_domain_address_fails_before_mutation(tmp_path: Path, answers):
    resolver, firewall = Resolver(), Firewall()
    resolver.answers = {"chatgpt.com": answers}
    enforcer = make_enforcer(tmp_path, firewall=firewall, resolver=resolver)
    with pytest.raises(OSError, match="no real IP"):
        enforcer.apply(payload())
    assert not firewall.rules
    assert not (tmp_path / "policy.json").exists()
    assert "chatgpt.com" not in (tmp_path / "hosts").read_text()


def test_resolved_gateway_overlap_is_rejected_before_mutation(tmp_path: Path):
    enforcer = make_enforcer(tmp_path, firewall=Firewall(["203.0.113.10"]))
    with pytest.raises(ValueError, match="protected Gateway"):
        enforcer.apply(payload())
    assert not (tmp_path / "policy.json").exists()
    assert "chatgpt.com" not in (tmp_path / "hosts").read_text()


def test_reapply_same_hash_new_session_replaces_owned_rules(tmp_path: Path):
    firewall = Firewall()
    enforcer = make_enforcer(tmp_path, firewall=firewall)
    enforcer.apply(payload())
    original_names = set(firewall.rules)
    enforcer.apply(payload(session="SES-OTHER"))
    assert original_names.isdisjoint(firewall.rules)


def test_domain_rules_use_all_protocols_and_correct_direction():
    installed = {}
    additions = []

    def runner(args, **kwargs):
        name = next((arg[5:] for arg in args if arg.startswith("name=")), "")
        if "show" in args:
            rule = installed.get(name)
            if rule is None:
                return subprocess.CompletedProcess(args, 1, "", "")
            direction, remote = rule
            output = (
                f"Rule Name: {name}\nDirection: {direction}\nAction: Block\n"
                f"RemoteIP: {remote}\nLocalIP: Any\n"
                "Enabled: Yes\nProtocol: Any\nProfiles: Domain,Private,Public\n"
            )
            return subprocess.CompletedProcess(args, 0, output, "")
        if "add" in args:
            additions.append(args)
            installed[name] = (
                "Inbound" if "dir=in" in args else "Outbound",
                next(arg[9:] for arg in args if arg.startswith("remoteip=")),
            )
        if "delete" in args:
            installed.pop(name, None)
        return subprocess.CompletedProcess(args, 0, "", "")

    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    rules = domain_traffic_rules(adapter.plan("SES-TRAFFIC", ["203.0.113.10"], []))
    adapter.reconcile(rules, ())
    assert len(additions) == 2
    assert all("protocol=any" in args and "profile=any" in args for args in additions)
    assert {arg for args in additions for arg in args if arg.startswith("dir=")} == {
        "dir=in",
        "dir=out",
    }
    # Direction verification must not accept an inbound rule as outbound.
    installed[rules[0].name] = ("Inbound", "203.0.113.10")
    assert not adapter.verify_rules(rules)
    adapter.remove_rules(rule.name for rule in rules)
    assert not installed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Direction", "In"),
        ("Enabled", "No"),
        ("Action", "Allow"),
        ("Protocol", "TCP"),
        ("Profiles", "Private"),
        ("LocalIP", "192.0.2.10"),
        ("RemoteIP", "203.0.113.0/24"),
    ],
)
def test_verification_rejects_rules_that_do_not_block_all_required_traffic(field, value):
    adapter = WindowsFirewallEnforcer(platform="win32")
    rule = domain_traffic_rules(adapter.plan("SES-TRAFFIC", ["203.0.113.10", "203.0.113.11"], []))[
        0
    ]
    fields = {
        "Rule Name": rule.name,
        "Direction": "Out",
        "Enabled": "Yes",
        "Action": "Block",
        "Protocol": "Any",
        "Profiles": "Domain,Private,Public",
        "LocalIP": "Any",
        # netsh may normalize individual addresses to equivalent prefixes.
        "RemoteIP": "203.0.113.10/31",
    }

    def runner(args, **kwargs):
        output = "\n".join(f"{key}: {item}" for key, item in fields.items())
        return subprocess.CompletedProcess(args, 0, output, "")

    adapter = WindowsFirewallEnforcer(runner=runner, platform="win32")
    assert adapter.verify_rules((rule,))
    fields[field] = value
    assert not adapter.verify_rules((rule,))
