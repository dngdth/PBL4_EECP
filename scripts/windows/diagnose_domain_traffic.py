"""Read-only comparison of policy DNS evidence, Firewall rules and live TCP flows.

Run elevated on the affected agent: python -m scripts.windows.diagnose_domain_traffic
--state <the Service's policy-state.json>. No hosts/rules/state changes are made.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from agent.infrastructure.windows.domain_resolver import WindowsDomainResolver

SNAPSHOT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$request = ConvertFrom-Json ([Console]::In.ReadToEnd())
$processes = @{}
foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
    $processes[[int]$process.Id] = $process.ProcessName
}
$flows = @(Get-NetTCPConnection -ErrorAction Stop | Where-Object {
    $_.State -in @('Established', 'SynSent', 'SynReceived', 'CloseWait')
} | ForEach-Object {
    @{remote_address=$_.RemoteAddress; remote_port=[int]$_.RemotePort;
      pid=[int]$_.OwningProcess; process=$processes[[int]$_.OwningProcess];
      state=[string]$_.State}
})
$rules = @(foreach ($name in $request.rule_names) {
    $rule = Get-NetFirewallRule -Name $name -PolicyStore ActiveStore -ErrorAction SilentlyContinue
    if ($null -eq $rule) { continue }
    $address = $rule | Get-NetFirewallAddressFilter
    $port = $rule | Get-NetFirewallPortFilter
    @{name=$rule.Name; enabled=[string]$rule.Enabled; action=[string]$rule.Action;
      direction=[string]$rule.Direction; profile=[string]$rule.Profile;
      protocol=[string]$port.Protocol; local_address=@($address.LocalAddress);
      remote_address=@($address.RemoteAddress);
      local_port=@($port.LocalPort); remote_port=@($port.RemotePort)}
})
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop | ForEach-Object {
    @{name=[string]$_.Name; enabled=[string]$_.Enabled}
})
@{flows=$flows; rules=$rules; profiles=$profiles} | ConvertTo-Json -Depth 8 -Compress
"""


def _contains(remote: str, values) -> bool:
    address = ipaddress.ip_address(remote)
    for value in values:
        for item in value.split(","):
            try:
                if address in ipaddress.ip_network(item.strip(), strict=False):
                    return True
            except ValueError:
                continue
    return False


def _any(values) -> bool:
    return values == ["Any"] or values == "Any"


def analyze(state: dict, snapshot: dict, answers: dict) -> dict:
    """IP matches are candidates, never proof of a flow's originating website."""
    stored = state.get("domain_addresses", {})
    flows = []
    for flow in snapshot["flows"]:
        remote = str(ipaddress.ip_address(flow["remote_address"]))
        if ipaddress.ip_address(remote).is_loopback:
            continue
        stored_domains = sorted(domain for domain, ips in stored.items() if _contains(remote, ips))
        current_domains = sorted(
            domain for domain, ips in answers.items() if _contains(remote, ips)
        )
        directions = set()
        for rule in snapshot["rules"]:
            if (
                str(rule["enabled"]).lower() == "true"
                and rule["action"] == "Block"
                and rule["protocol"] == "Any"
                and rule["profile"] == "Any"
                and _any(rule["local_address"])
                and _any(rule["local_port"])
                and _any(rule["remote_port"])
                and _contains(remote, rule["remote_address"])
            ):
                directions.add(rule["direction"])
        candidate = sorted(set(stored_domains) | set(current_domains))
        flows.append(
            {
                **flow,
                "candidate_policy_domains": candidate,
                "stored_domains": stored_domains,
                "current_dns_domains": current_domains,
                "configured_directions": sorted(directions),
                "assessment": (
                    "unattributed"
                    if not candidate
                    else "configured-not-packet-verified"
                    if {"Inbound", "Outbound"} <= directions
                    else "missing-or-mismatched-rule"
                ),
            }
        )
    missing = defaultdict(list)
    for domain, addresses in answers.items():
        for address in addresses:
            if not _contains(address, stored.get(domain, [])):
                missing[domain].append(address)
    return {
        "session_id": state.get("session_id"),
        "policy_hash": state.get("policy_hash"),
        "mode": state.get("mode"),
        "profiles": snapshot["profiles"],
        "disabled_profiles": [
            profile["name"]
            for profile in snapshot["profiles"]
            if str(profile["enabled"]).lower() != "true"
        ],
        "new_dns_addresses_not_in_state": dict(missing),
        "rules": snapshot["rules"],
        "tcp_flows": flows,
        "domain_resolution": state.get("domain_resolution", {}),
        "packet_enforcement": "not-verified",
        "udp_quic": "not-observed-by-this-TCP-snapshot",
    }


def collect(state: dict, runner=subprocess.run) -> dict:
    result = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SNAPSHOT_SCRIPT],
        input=json.dumps({"rule_names": state.get("managed_firewall_rules", [])}),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise OSError(
            "cannot read TCP/Firewall snapshot; run on the Windows agent as Administrator"
        )
    snapshot = json.loads(result.stdout)
    if not isinstance(snapshot, dict) or any(
        not isinstance(snapshot.get(key), list) for key in ("flows", "rules", "profiles")
    ):
        raise OSError("invalid TCP/Firewall snapshot")
    resolver = WindowsDomainResolver(runner=runner)
    error = None
    try:
        answers = resolver.resolve(state.get("blocked_domains", []))
    except (OSError, ValueError) as exc:
        answers, error = {}, str(exc)
    report = analyze(state, snapshot, answers)
    report["dns_discovery_error"] = error
    report["current_dns_evidence"] = resolver.last_evidence
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Run this diagnostic on the affected Windows agent")
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("invalid policy state")
        print(json.dumps(collect(state), indent=2, ensure_ascii=False))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"Diagnostic failed: {exc}") from None


if __name__ == "__main__":
    main()
