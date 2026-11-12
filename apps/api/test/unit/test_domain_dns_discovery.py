from __future__ import annotations

import json
import subprocess
import sys

import pytest

from agent.domain.policy import CATEGORY_DOMAINS
from agent.infrastructure.windows.domain_resolver import WindowsDomainResolver
from apps.api.test.unit.test_domain_traffic import Firewall, make_enforcer, payload
from scripts.windows.diagnose_domain_traffic import analyze, collect


class DnsRunner:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(self.rows), "")


ALL_POLICY_DOMAINS = sorted(set().union(*CATEGORY_DOMAINS.values(), {"custom.example"}))


@pytest.mark.parametrize("domain", ALL_POLICY_DOMAINS)
def test_every_policy_domain_follows_cached_cname_to_outside_cdn(domain):
    runner = DnsRunner(
        [
            {"name": "api." + domain, "cname": "edge.cdn.example", "source": "windows-cache"},
            {"name": "edge.cdn.example", "cname": "pool.cdn.example", "source": "windows-cache"},
            {"name": "pool.cdn.example", "address": "203.0.113.17", "source": "windows-cache"},
            {"name": "pool.cdn.example", "address": "2001:db8::17", "source": "windows-cache"},
            {"name": "other.cdn.example", "address": "203.0.113.99"},
            {"name": domain + ".attacker.example", "address": "203.0.113.98"},
            {"name": "innocent.example", "cname": "pool.cdn.example"},
            {"name": "innocent.example", "address": "203.0.113.97"},
        ]
    )
    resolver = WindowsDomainResolver(runner=runner)
    assert resolver.resolve([domain]) == {domain: ("2001:db8::17", "203.0.113.17")}
    evidence = resolver.last_evidence[domain]
    assert all(
        row["cname_chain"] == ["api." + domain, "edge.cdn.example", "pool.cdn.example"]
        for row in evidence
    )
    assert {row["source"] for row in evidence} == {"windows-cache"}


def test_cname_cycles_terminate_without_reverse_or_sibling_attribution():
    resolver = WindowsDomainResolver(
        runner=DnsRunner(
            [
                {"name": "blocked.example", "cname": "edge.cdn.example"},
                {"name": "edge.cdn.example", "cname": "blocked.example"},
                {"name": "unrelated.example", "cname": "edge.cdn.example"},
                {"name": "unrelated.example", "address": "203.0.113.99"},
                {"name": "api.edge.cdn.example", "address": "203.0.113.98"},
            ]
        )
    )
    assert resolver.resolve(["blocked.example"]) == {"blocked.example": ()}


def test_prepolicy_history_survives_cache_expiry_but_is_bounded():
    now = [0.0]
    runner = DnsRunner([{"name": "api.custom.example", "address": "203.0.113.18"}])
    resolver = WindowsDomainResolver(
        runner=runner,
        observe_cache=True,
        clock=lambda: now[0],
        history_seconds=10,
    )
    resolver.observe()
    assert "Resolve-DnsName" not in runner.calls[0][0][-1]
    runner.rows = []
    now[0] = 5.0
    assert resolver.resolve(["custom.example"]) == {"custom.example": ("203.0.113.18",)}
    assert json.loads(runner.calls[-1][1]["input"])["known_names"] == ["api.custom.example"]
    assert resolver.last_evidence["custom.example"][0]["source"] == "dns-history"
    now[0] = 11.0
    assert resolver.cached_addresses(["custom.example"]) == {"custom.example": ()}

    bounded = WindowsDomainResolver(runner=runner, observe_cache=True, max_history_records=1)
    for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        runner.rows = [{"name": "custom.example", "address": ip}]
        bounded.observe()
    assert bounded.cached_addresses(["custom.example"]) == {"custom.example": ("203.0.113.3",)}


def test_cached_ip_updates_firewall_before_full_dns_refresh_and_survives_restart(tmp_path):
    now = [0.0]
    runner = DnsRunner(
        [
            {"name": "api.custom.example", "cname": "edge.cdn.example"},
            {"name": "edge.cdn.example", "address": "203.0.113.20"},
        ]
    )
    resolver = WindowsDomainResolver(runner=runner, observe_cache=True, clock=lambda: now[0])
    firewall = Firewall()
    enforcer = make_enforcer(tmp_path, resolver=resolver, firewall=firewall, clock=lambda: now[0])
    enforcer.maintain()  # No policy yet: Service captures cache evidence.
    runner.rows = []
    enforcer.apply(payload(["custom.example"]))
    state = json.loads((tmp_path / "policy.json").read_text())
    assert (
        state["domain_resolution"]["custom.example"]["observations"][0]["source"] == "dns-history"
    )
    assert state["network_enforcement"]["packet_enforcement"] == "not-verified"
    full_queries = sum("Resolve-DnsName" in args[-1] for args, _ in runner.calls)
    runner.rows = [{"name": "edge.cdn.example", "address": "2001:db8::21"}]
    now[0] = 5.0
    enforcer.maintain()
    assert sum("Resolve-DnsName" in args[-1] for args, _ in runner.calls) == full_queries
    assert {rule.remote_address for rule in firewall.rules.values()} == {
        "2001:db8::21,203.0.113.20",
    }
    firewall.rules.clear()
    restarted = make_enforcer(tmp_path, firewall=firewall)
    restarted.maintain()
    assert {rule.remote_address for rule in firewall.rules.values()} == {
        "2001:db8::21,203.0.113.20",
    }
    enforcer.restore()
    assert not firewall.rules


def test_observation_failure_does_not_skip_firewall_integrity_repair(tmp_path):
    class FailingObserver:
        def resolve(self, domains):
            return {domain: ("203.0.113.10",) for domain in domains}

        def observe(self):
            raise OSError("cache unavailable")

    firewall = Firewall()
    enforcer = make_enforcer(tmp_path, resolver=FailingObserver(), firewall=firewall)
    enforcer.apply(payload())
    firewall.rules.clear()
    enforcer.maintain()
    assert len(firewall.rules) == 2
    assert "DNS cache observation failed" in enforcer.health_error


def test_cached_refresh_failure_preserves_previous_state_and_rules(tmp_path):
    runner = DnsRunner([{"name": "custom.example", "address": "203.0.113.10"}])
    resolver, firewall = WindowsDomainResolver(runner=runner, observe_cache=True), Firewall()
    enforcer = make_enforcer(tmp_path, resolver=resolver, firewall=firewall)
    enforcer.apply(payload(["custom.example"]))
    old_state, old_rules = (tmp_path / "policy.json").read_bytes(), dict(firewall.rules)
    runner.rows = [{"name": "api.custom.example", "address": "203.0.113.11"}]
    firewall.fail = True
    with pytest.raises(OSError, match="partial firewall"):
        enforcer.maintain()
    assert (tmp_path / "policy.json").read_bytes() == old_state
    assert firewall.rules == old_rules


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell cache fields")
def test_powershell_handles_numeric_and_named_types_and_queries_cached_subdomains():
    def runner(args, **kwargs):
        args = list(args)
        args[-1] = (
            "function Get-DnsClientCache {\n"
            " [pscustomobject]@{Name='api.custom.example';Type=5;Data='edge.cdn.example'}\n"
            " [pscustomobject]@{Name='edge.cdn.example';Type=28;Data='2001:db8::8'}\n"
            " [pscustomobject]@{RecordName='custom.example';RecordType='A';Data='203.0.113.8'}\n"
            "}\n"
            "function Resolve-DnsName { param($Name)\n"
            " if($Name -eq 'api.custom.example') { [pscustomobject]@{IPAddress='203.0.113.9'} }\n"
            "}\n" + args[-1]
        )
        return subprocess.run(args, **kwargs)

    resolver = WindowsDomainResolver(runner=runner)
    assert resolver.resolve(["custom.example"]) == {
        "custom.example": ("2001:db8::8", "203.0.113.8", "203.0.113.9"),
    }


def test_diagnostic_distinguishes_unknown_flows_missing_rules_and_configured_rules():
    state = {"domain_addresses": {"custom.example": ["203.0.113.10"]}}
    rule = {
        "enabled": "True",
        "action": "Block",
        "protocol": "Any",
        "profile": "Any",
        "local_address": ["Any"],
        "local_port": ["Any"],
        "remote_port": ["Any"],
        "remote_address": ["203.0.113.10"],
    }
    snapshot = {
        "profiles": [{"name": "Public", "enabled": "False"}],
        "rules": [{**rule, "direction": direction} for direction in ("Inbound", "Outbound")],
        "flows": [
            {"remote_address": address}
            for address in (
                "203.0.113.10",
                "203.0.113.11",
                "203.0.113.99",
            )
        ],
    }
    report = analyze(state, snapshot, {"custom.example": ["203.0.113.10", "203.0.113.11"]})
    assert [flow["assessment"] for flow in report["tcp_flows"]] == [
        "configured-not-packet-verified",
        "missing-or-mismatched-rule",
        "unattributed",
    ]
    assert report["new_dns_addresses_not_in_state"] == {"custom.example": ["203.0.113.11"]}
    assert report["disabled_profiles"] == ["Public"]
    assert report["udp_quic"] == "not-observed-by-this-TCP-snapshot"


def test_diagnostic_keeps_firewall_snapshot_when_dns_discovery_fails():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if "Get-NetTCPConnection" in args[-1]:
            return subprocess.CompletedProcess(args, 0, '{"flows":[],"rules":[],"profiles":[]}', "")
        return subprocess.CompletedProcess(args, 1, "", "DNS unavailable")

    report = collect({"blocked_domains": ["custom.example"], "managed_firewall_rules": []}, runner)
    assert report["dns_discovery_error"]
    assert report["tcp_flows"] == []
    assert json.loads(calls[0][1]["input"]) == {"rule_names": []}


def test_domain_resolver_throttles_passive_cache_observation():
    now = [0.0]
    runner = DnsRunner(
        [{"name": "cdn.example.com", "address": "198.51.100.10", "source": "windows-cache"}]
    )

    resolver = WindowsDomainResolver(
        runner=runner,
        observe_cache=True,
        observe_interval_seconds=20.0,
        clock=lambda: now[0],
    )
    resolver.observe()
    now[0] = 10.0
    resolver.observe()
    now[0] = 20.0
    resolver.observe()

    assert len(runner.calls) == 2

