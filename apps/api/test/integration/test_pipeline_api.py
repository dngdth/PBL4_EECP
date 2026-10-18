from pathlib import Path

from app.main import create_app
from fastapi.testclient import TestClient


def test_complete_exam_pipeline(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "pipeline.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Lập trình C - No AI",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01", "PC02", "PC03"],
                "actor": "teacher-01",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        deployed = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "PROGRAMMING_EXAM",
                "actor": "teacher-01",
                "rules": {
                    "applications": {
                        "allow": ["vscode.exe", "gcc.exe"],
                        "deny": ["anydesk.exe", "teamviewer.exe"],
                    },
                    "network": {
                        "blocked_categories": ["generative_ai", "vpn_proxy"],
                        "allow_domains": ["lms.dut.udn.vn", "cppreference.com"],
                    },
                    "devices": {"usb_storage": "deny"},
                },
            },
        )
        assert deployed.status_code == 200
        assert deployed.json()["state"] == "DEPLOYING"
        policy_hash = deployed.json()["policy"]["policy_hash"]

        for target_id in ["gw-f301", "PC01", "PC02", "PC03"]:
            commands = client.get(f"/api/v1/agents/{target_id}/commands").json()
            assert len(commands) == 1
            acknowledged = client.post(
                f"/api/v1/commands/{commands[0]['id']}/acknowledge",
                json={"success": True, "policy_hash": policy_hash, "actor": target_id},
            )
            assert acknowledged.status_code == 200

        assert acknowledged.json()["state"] == "PREFLIGHT"

        checks = [
            {"name": "agent_online", "passed": True, "critical": True},
            {"name": "policy_compliant", "passed": True, "critical": True},
            {"name": "dns_policy", "passed": True, "critical": True},
            {"name": "exam_app", "passed": True, "critical": True},
            {"name": "clock_sync", "passed": True, "critical": False},
        ]
        for workstation_id in ["PC01", "PC02", "PC03"]:
            preflight = client.post(
                f"/api/v1/sessions/{session_id}/workstations/{workstation_id}/preflight",
                json={"checks": checks, "actor": workstation_id},
            )
            assert preflight.status_code == 200
        assert preflight.json()["state"] == "READY"

        started = client.post(
            f"/api/v1/sessions/{session_id}/start",
            json={"actor": "teacher-01"},
        )
        assert started.status_code == 200
        assert started.json()["state"] == "RUNNING"

        blocked = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "NETWORK_BLOCK",
                "severity": "INFO",
                "category": "GENERATIVE_AI",
                "action": "BLOCKED",
                "destination": "chatgpt.com",
            },
        )
        assert blocked.status_code == 202
        assert blocked.json()["incident_id"] is None

        incident_id = None
        for workstation_id in ["PC01", "PC02", "PC03"]:
            dns_failure = client.post(
                f"/api/v1/sessions/{session_id}/telemetry",
                json={
                    "workstation_id": workstation_id,
                    "event_type": "DNS_FAILURE",
                    "severity": "WARNING",
                    "category": "DNS",
                    "action": "OBSERVED",
                },
            )
            assert dns_failure.status_code == 202
            incident_id = dns_failure.json()["incident_id"] or incident_id
        assert incident_id is not None

        finished = client.post(
            f"/api/v1/sessions/{session_id}/finish",
            json={"actor": "teacher-01"},
        )
        assert finished.status_code == 200
        assert finished.json()["state"] == "RESTORING"

        for target_id in ["gw-f301", "PC01", "PC02", "PC03"]:
            commands = client.get(f"/api/v1/agents/{target_id}/commands").json()
            restore = next(command for command in commands if command["type"] == "RESTORE_BASELINE")
            acknowledged = client.post(
                f"/api/v1/commands/{restore['id']}/acknowledge",
                json={"success": True, "actor": target_id},
            )
            assert acknowledged.status_code == 200

        assert acknowledged.json()["state"] == "NORMAL"
        summary = client.get(f"/api/v1/sessions/{session_id}/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["session"]["state"] == "NORMAL"
        assert body["telemetry_count"] == 4
        assert body["blocked_event_count"] == 1
        assert len(body["incidents"]) == 1
        assert body["incidents"][0]["category"] == "INFRASTRUCTURE_DNS"
        assert body["audit_chain_valid"] is True


def test_start_gate_rejects_unready_session(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "gate.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Exam",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()

        response = client.post(
            f"/api/v1/sessions/{created['id']}/start",
            json={"actor": "teacher-01"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "READINESS_GATE"


def test_deploy_policy_rejects_incompatible_protocol_v2_rules(tmp_path: Path) -> None:
    from app.presentation.api.routers.gateways import _to_contract_command
    from app.domain.entities.operations import Command as DomainCommand

    with TestClient(create_app(tmp_path / "reject-incompatible.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Kiem tra",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()
        session_id = created["id"]

        # 1. Reject strict_mode in network
        res1 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD",
                "actor": "teacher-01",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": []},
                    "network": {"strict_mode": True},
                    "devices": {"usb": "deny"},
                },
            },
        )
        assert res1.status_code == 422

        # 2. Reject network_lockdown in network
        res2 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD",
                "actor": "teacher-01",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": []},
                    "network": {"network_lockdown": True},
                    "devices": {"usb": "deny"},
                },
            },
        )
        assert res2.status_code == 422

        # 3. Reject usb_storage_blocked at root of rules
        res3 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD",
                "actor": "teacher-01",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": []},
                    "network": {"block": ["generative_ai"]},
                    "usb_storage_blocked": True,
                },
            },
        )
        assert res3.status_code == 422

        # 4. Reject CRLF / newline injection in blocked_domains
        res4 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD",
                "actor": "teacher-01",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": []},
                    "network": {
                        "block": ["generative_ai"],
                        "blocked_domains": ["example.com\n127.0.0.1 evil.com"],
                    },
                    "devices": {"usb": "deny"},
                },
            },
        )
        assert res4.status_code == 422

        # 5. Reject comment injection in blocked_domains
        res5 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD",
                "actor": "teacher-01",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": []},
                    "network": {
                        "block": ["generative_ai"],
                        "blocked_domains": ["evil.com#comment"],
                    },
                    "devices": {"usb": "deny"},
                },
            },
        )
        assert res5.status_code == 422

        # Verify session state is still CREATED and no commands were created
        current = client.get(f"/api/v1/sessions/{session_id}").json()
        assert current["state"] == "CREATED"
        commands = client.get("/api/v1/agents/PC01/commands").json()
        assert len(commands) == 0


def test_deploy_policy_accepts_valid_protocol_v2_rules_and_verifies_command(
    tmp_path: Path,
) -> None:
    from app.presentation.api.routers.gateways import _to_contract_command
    from app.domain.entities.operations import Command as DomainCommand
    from contracts.v2 import Command as ContractCommand, CommandType as ContractCommandType

    with TestClient(create_app(tmp_path / "accept-valid.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Kiem tra hop le",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()
        session_id = created["id"]

        valid_rules = {
            "applications": {
                "allow": ["exam-browser.exe", "calculator.exe"],
                "deny": ["anydesk.exe"],
            },
            "network": {
                "block": ["generative_ai", "vpn_proxy"],
                "allow_domains": ["lms.dut.udn.vn"],
            },
            "devices": {"usb": "deny"},
        }

        deployed = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD_EXAM_POLICY",
                "actor": "teacher-01",
                "rules": valid_rules,
            },
        )
        assert deployed.status_code == 200
        data = deployed.json()
        assert data["state"] == "DEPLOYING"
        assert data["policy"]["policy_hash"] is not None

        # Verify command was delivered and can be converted to Protocol v2 contract
        commands = client.get("/api/v1/agents/PC01/commands").json()
        assert len(commands) == 1
        cmd_raw = commands[0]
        assert cmd_raw["type"] == "APPLY_POLICY"

        from datetime import datetime
        from app.domain.value_objects.enums import CommandType as DomainCommandType

        domain_cmd = DomainCommand(
            id=cmd_raw["id"],
            session_id=cmd_raw["session_id"],
            target_id=cmd_raw["target_id"],
            type=DomainCommandType(cmd_raw["type"]),
            payload=cmd_raw["payload"],
            created_at=datetime.fromisoformat(cmd_raw["created_at"]),
            expires_at=datetime.fromisoformat(cmd_raw["expires_at"]),
        )
        contract_cmd = _to_contract_command(domain_cmd)
        assert isinstance(contract_cmd, ContractCommand)
        assert contract_cmd.command_type == ContractCommandType.APPLY_POLICY
        assert contract_cmd.payload.policy.policy_hash == data["policy"]["policy_hash"]
        assert contract_cmd.payload.policy.rules.devices.usb == "deny"

        # Verify policy hash consistency with Protocol v2
        from contracts.v2 import PolicyRules as ContractPolicyRules, compute_policy_hash
        expected_hash = compute_policy_hash(
            "STANDARD_EXAM_POLICY",
            1,
            ContractPolicyRules.model_validate(valid_rules),
        )
        assert data["policy"]["policy_hash"] == expected_hash

        # Verify Windows Agent can parse the command payload directly
        from agent.domain.policy import parse_policy_payload
        spec = parse_policy_payload(cmd_raw["payload"])
        assert spec.policy_hash == expected_hash
        assert spec.denied_applications == ("anydesk.exe",)
        assert set(spec.blocked_categories) == {"generative_ai", "vpn_proxy"}
        assert spec.usb_deny is True


def test_deploy_policy_rejects_overlapping_application_rules(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "overlap.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Kiem tra trung lap",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()
        session_id = created["id"]

        overlap_rules = {
            "applications": {
                "allow": ["exam-browser.exe"],
                "deny": ["exam-browser.exe"],
            },
            "network": {"block": ["generative_ai"]},
            "devices": {"usb": "deny"},
        }

        res = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={
                "profile": "STANDARD_EXAM_POLICY",
                "actor": "teacher-01",
                "rules": overlap_rules,
            },
        )
        assert res.status_code == 422
        current = client.get(f"/api/v1/sessions/{session_id}").json()
        assert current["state"] == "CREATED"


def test_presence_hash_does_not_satisfy_policy_ack_and_gateway_ack_lifecycle(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "ack-lifecycle.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Kiem tra ACK lifecycle",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()
        session_id = created["id"]

        rules = {
            "applications": {"allow": ["exam-browser.exe"], "deny": ["cheat.exe"]},
            "network": {"block": ["generative_ai"]},
            "devices": {"usb": "deny"},
        }
        deployed = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={"profile": "STANDARD_EXAM_POLICY", "rules": rules},
        ).json()
        policy_hash = deployed["policy"]["policy_hash"]
        assert deployed["state"] == "DEPLOYING"

        # 1. Check that presence alone does NOT satisfy ACK:
        # actual_policy_hash is None, policy_compliant is False, gateway_policy_hash is None
        session = client.get(f"/api/v1/sessions/{session_id}").json()
        ws = session["workstations"]["PC01"]
        assert ws["actual_policy_hash"] is None
        assert ws["policy_compliant"] is False
        assert session["gateway_policy_hash"] is None
        assert session["state"] == "DEPLOYING"

        # 2. Deliver and ACK workstation command
        pc_commands = client.get("/api/v1/agents/PC01/commands").json()
        assert len(pc_commands) == 1
        pc_cmd_id = pc_commands[0]["id"]
        ack_pc = client.post(
            f"/api/v1/commands/{pc_cmd_id}/acknowledge",
            json={"success": True, "policy_hash": policy_hash, "actor": "PC01"},
        ).json()
        assert ack_pc["workstations"]["PC01"]["actual_policy_hash"] == policy_hash
        assert ack_pc["workstations"]["PC01"]["policy_compliant"] is True

        # Workstation is compliant, BUT Gateway has NOT ACKed -> Session MUST remain DEPLOYING!
        assert ack_pc["gateway_policy_hash"] is None
        assert ack_pc["state"] == "DEPLOYING"

        # 3. Deliver and ACK Gateway command
        gw_commands = client.get("/api/v1/agents/gw-f301/commands").json()
        assert len(gw_commands) == 1
        gw_cmd_id = gw_commands[0]["id"]
        ack_gw = client.post(
            f"/api/v1/commands/{gw_cmd_id}/acknowledge",
            json={"success": True, "policy_hash": policy_hash, "actor": "gw-f301"},
        ).json()

        # Both workstation and Gateway have ACKed -> state transitions to PREFLIGHT
        assert ack_gw["gateway_policy_hash"] == policy_hash
        assert ack_gw["workstations"]["PC01"]["policy_compliant"] is True
        assert ack_gw["state"] == "PREFLIGHT"


def test_workstation_policy_failure_transitions_session_to_degraded(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "ack-failure.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Kiem tra policy failure",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()
        session_id = created["id"]

        rules = {
            "applications": {"deny": ["cheat.exe"]},
            "network": {"block": ["generative_ai"]},
            "devices": {"usb": "deny"},
        }
        client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={"profile": "STANDARD_EXAM_POLICY", "rules": rules},
        )

        pc_commands = client.get("/api/v1/agents/PC01/commands").json()
        pc_cmd_id = pc_commands[0]["id"]
        failed_session = client.post(
            f"/api/v1/commands/{pc_cmd_id}/acknowledge",
            json={"success": False, "error": "Access denied to hosts file", "actor": "PC01"},
        ).json()

        assert failed_session["state"] == "DEGRADED"
        assert failed_session["workstations"]["PC01"]["readiness"] == "FAILED"


def test_finished_session_rejects_policy_deployment(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "finished-session.db")) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "exam_name": "Kiem tra finished",
                "room_id": "F301",
                "gateway_id": "gw-f301",
                "workstation_ids": ["PC01"],
            },
        ).json()
        session_id = created["id"]

        rules = {
            "applications": {"deny": ["cheat.exe"]},
            "network": {"block": ["generative_ai"]},
            "devices": {"usb": "deny"},
        }
        client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={"profile": "STANDARD_EXAM_POLICY", "rules": rules},
        )

        # Acknowledge both targets to transition to PREFLIGHT
        gw_cmd = client.get("/api/v1/agents/gw-f301/commands").json()[0]
        pc_cmd = client.get("/api/v1/agents/PC01/commands").json()[0]
        hash_val = client.get(f"/api/v1/sessions/{session_id}").json()["policy"]["policy_hash"]
        client.post(f"/api/v1/commands/{gw_cmd['id']}/acknowledge", json={"success": True, "policy_hash": hash_val, "actor": "gw-f301"})
        client.post(f"/api/v1/commands/{pc_cmd['id']}/acknowledge", json={"success": True, "policy_hash": hash_val, "actor": "PC01"})

        # Submit preflight to make workstation READY
        client.post(
            f"/api/v1/sessions/{session_id}/workstations/PC01/preflight",
            json={"checks": [{"name": "exam_app", "passed": True}]},
        )
        # Start and Finish session
        client.post(f"/api/v1/sessions/{session_id}/start", json={"force": True, "reason": "test"})
        client.post(f"/api/v1/sessions/{session_id}/finish", json={"actor": "teacher"})

        # After finish, session enters RESTORING state while baseline is restored
        restoring = client.get(f"/api/v1/sessions/{session_id}").json()
        assert restoring["state"] == "RESTORING"

        # Attempt to deploy policy on RESTORING session -> 409 INVALID_STATE
        res1 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={"profile": "STANDARD_EXAM_POLICY", "rules": rules},
        )
        assert res1.status_code == 409
        assert res1.json()["code"] == "INVALID_STATE"

        # Acknowledge restore commands for gateway and PC01
        gw_restore = client.get("/api/v1/agents/gw-f301/commands").json()[0]
        pc_restore = client.get("/api/v1/agents/PC01/commands").json()[0]
        client.post(f"/api/v1/commands/{gw_restore['id']}/acknowledge", json={"success": True, "actor": "gw-f301"})
        client.post(f"/api/v1/commands/{pc_restore['id']}/acknowledge", json={"success": True, "actor": "PC01"})

        normal = client.get(f"/api/v1/sessions/{session_id}").json()
        assert normal["state"] == "NORMAL"

        # Attempt to deploy policy on NORMAL session -> 409 INVALID_STATE
        res2 = client.post(
            f"/api/v1/sessions/{session_id}/policy/deploy",
            json={"profile": "STANDARD_EXAM_POLICY", "rules": rules},
        )
        assert res2.status_code == 409
        assert res2.json()["code"] == "INVALID_STATE"
