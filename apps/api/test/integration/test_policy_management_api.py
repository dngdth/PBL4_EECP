from dataclasses import replace
from pathlib import Path

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.security import Principal, Role
from app.domain.exceptions.errors import InvalidStateTransitionError
from app.domain.value_objects.enums import CommandStatus
from app.infrastructure.security import TokenService
from app.main import create_app
from fastapi.testclient import TestClient


def _register(client: TestClient, agent_id: str) -> None:
    response = client.post(
        "/api/v1/agents/register",
        json={
            "id": agent_id,
            "hostname": f"HOST-{agent_id}",
            "ip_address": "192.168.3.56",
            "agent_version": "1.1.0",
        },
    )
    assert response.status_code == 201


def test_profile_catalog_exposes_generated_yaml(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "profiles.db")) as client:
        response = client.get("/api/v1/policy-profiles")

    assert response.status_code == 200
    profile = response.json()[0]
    assert profile["id"] == "INTERNET_NO_AI"
    assert profile["label"] == "Internet + No AI"
    assert profile["is_builtin"] is True
    assert "policy:" in profile["yaml"]
    assert "generative_ai" in profile["yaml"]
    assert profile["rules"]["devices"]["usb"] == "deny"


def test_new_session_pushes_policy_and_restores_after_finish(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "policy-flow.db")) as client:
        _register(client, "PC01")
        created = client.post(
            "/api/v1/sessions",
            json={
                "name": "PBL4 Final",
                "room": "A101",
                "agent_ids": ["PC01"],
                "policy_profile": "INTERNET_NO_AI",
            },
        )
        assert created.status_code == 201
        session = created.json()
        session_id = session["id"]
        assert session["policy"]["profile"] == "INTERNET_NO_AI"
        assert session["agents"][0]["policy_status"] == "PENDING"

        commands = client.get("/api/v1/agents/PC01/commands").json()
        assert len(commands) == 1
        apply_command = commands[0]
        assert apply_command["type"] == "APPLY_POLICY"
        assert apply_command["status"] == "PENDING"
        assert apply_command["attempt_count"] == 0
        assert apply_command["last_attempt_at"] is None
        assert apply_command["next_retry_at"] is None
        assert apply_command["expires_at"] is not None
        assert apply_command["payload"]["format"] == "eecp-policy/v1"
        policy_hash = apply_command["payload"]["policy_hash"]

        acknowledged = client.post(
            f"/api/v1/commands/{apply_command['id']}/acknowledge",
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC01",
            },
        )
        assert acknowledged.status_code == 200
        detail = client.get(f"/api/v1/sessions/{session_id}").json()
        assert detail["agents"][0]["policy_status"] == "APPLIED"

        for target in ("READY", "RUNNING", "FINISHED"):
            transitioned = client.patch(
                f"/api/v1/sessions/{session_id}/status",
                json={"status": target},
            )
            assert transitioned.status_code == 200

        commands = client.get("/api/v1/agents/PC01/commands").json()
        assert len(commands) == 1
        restore_command = commands[0]
        assert restore_command["type"] == "RESTORE_BASELINE"
        restored = client.post(
            f"/api/v1/commands/{restore_command['id']}/acknowledge",
            json={"success": True, "actor": "PC01"},
        )
        assert restored.status_code == 200
        detail = client.get(f"/api/v1/sessions/{session_id}").json()
        assert detail["status"] == "FINISHED"
        assert detail["agents"][0]["policy_status"] == "RESTORED"


def test_unknown_policy_profile_is_rejected_without_creating_session(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "unknown-profile.db")) as client:
        _register(client, "PC01")
        response = client.post(
            "/api/v1/sessions",
            json={
                "name": "Exam",
                "room": "A101",
                "agent_ids": ["PC01"],
                "policy_profile": "NOT_A_PROFILE",
            },
        )

        assert response.status_code == 422
        assert response.json()["code"] == "DOMAIN_VALIDATION"
        assert client.get("/api/v1/sessions").json() == []


def _custom_profile_body(profile_id: str = "browser_research") -> dict:
    return {
        "id": profile_id,
        "label": "Browser Research",
        "description": "Allow browser research while blocking AI and remote control.",
        "rules": {
            "applications": {
                "allow": ["VSCODE.EXE", "chrome.exe"],
                "deny": ["anydesk.exe"],
            },
            "network": {"block": ["generative_ai"]},
            "devices": {"usb": "deny"},
        },
    }


def test_teacher_can_create_update_and_delete_custom_profile(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "profile-crud.db")) as client:
        created = client.post("/api/v1/policy-profiles", json=_custom_profile_body())
        assert created.status_code == 201
        assert created.json()["id"] == "BROWSER_RESEARCH"
        assert created.json()["is_builtin"] is False
        assert created.json()["rules"]["applications"]["allow"][0] == "vscode.exe"

        updated = client.put(
            "/api/v1/policy-profiles/BROWSER_RESEARCH",
            json={
                "label": "Research without AI",
                "description": "Updated teacher-managed profile.",
                "rules": {
                    "network": {"block": ["generative_ai", "social_network"]},
                    "devices": {"usb": "allow"},
                },
            },
        )
        assert updated.status_code == 200
        assert updated.json()["label"] == "Research without AI"
        assert updated.json()["rules"]["devices"]["usb"] == "allow"

        deleted = client.delete("/api/v1/policy-profiles/BROWSER_RESEARCH")
        assert deleted.status_code == 204
        assert all(
            item["id"] != "BROWSER_RESEARCH"
            for item in client.get("/api/v1/policy-profiles").json()
        )


def test_custom_profile_is_persisted_and_can_create_session(tmp_path: Path) -> None:
    path = tmp_path / "profile-persistence.db"
    with TestClient(create_app(path)) as client:
        assert client.post(
            "/api/v1/policy-profiles", json=_custom_profile_body()
        ).status_code == 201

    with TestClient(create_app(path)) as client:
        _register(client, "PC01")
        profiles = client.get("/api/v1/policy-profiles").json()
        assert any(item["id"] == "BROWSER_RESEARCH" for item in profiles)

        session = client.post(
            "/api/v1/sessions",
            json={
                "name": "Research Exam",
                "room": "A101",
                "agent_ids": ["PC01"],
                "policy_profile": "BROWSER_RESEARCH",
            },
        )
        assert session.status_code == 201
        assert session.json()["policy"]["profile"] == "BROWSER_RESEARCH"
        original_policy = session.json()["policy"]

        updated = client.put(
            "/api/v1/policy-profiles/BROWSER_RESEARCH",
            json={
                "label": "Changed for future sessions",
                "description": "Existing session snapshots must remain unchanged.",
                "rules": {"devices": {"usb": "allow"}},
            },
        )
        assert updated.status_code == 200
        persisted_session = client.get(
            f"/api/v1/sessions/{session.json()['id']}"
        ).json()
        assert persisted_session["policy"] == original_policy

        in_use = client.delete("/api/v1/policy-profiles/BROWSER_RESEARCH")
        assert in_use.status_code == 409
        assert in_use.json()["code"] == "POLICY_IN_USE"


def test_built_in_profiles_are_read_only_and_cannot_be_deleted(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "built-in-profiles.db")) as client:
        update = client.put(
            "/api/v1/policy-profiles/INTERNET_NO_AI",
            json={
                "label": "Changed",
                "description": "Should not be accepted.",
                "rules": {},
            },
        )
        deleted = client.delete("/api/v1/policy-profiles/INTERNET_NO_AI")

    assert update.status_code == 422
    assert update.json()["code"] == "DOMAIN_VALIDATION"
    assert deleted.status_code == 422
    assert deleted.json()["code"] == "DOMAIN_VALIDATION"


def test_custom_profile_rejects_conflicting_application_rules(tmp_path: Path) -> None:
    body = _custom_profile_body()
    body["rules"]["applications"]["deny"].append("VSCODE.EXE")

    with TestClient(create_app(tmp_path / "invalid-profile.db")) as client:
        response = client.post("/api/v1/policy-profiles", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "DOMAIN_VALIDATION"
    assert "both allowed and denied" in response.json()["detail"]


def test_acknowledge_command_api_regression_suite(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "ack-regression.db")) as client:
        _register(client, "PC01")
        _register(client, "PC02")
        created = client.post(
            "/api/v1/sessions",
            json={
                "name": "PBL4 Final",
                "room": "A101",
                "agent_ids": ["PC01"],
                "policy_profile": "INTERNET_NO_AI",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        commands = client.get("/api/v1/agents/PC01/commands").json()
        assert len(commands) == 1
        apply_command = commands[0]
        cmd_id = apply_command["id"]
        policy_hash = apply_command["payload"]["policy_hash"]

        # 1. ACK sai Agent (PC02 attempts to ACK PC01's command) -> 422 DOMAIN_VALIDATION
        wrong_ack = client.post(
            f"/api/v1/commands/{cmd_id}/acknowledge",
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC02",
            },
        )
        assert wrong_ack.status_code == 422
        assert wrong_ack.json()["code"] == "DOMAIN_VALIDATION"
        assert "target PC01 does not match actor PC02" in wrong_ack.json()["detail"]

        # Verify command remains DELIVERED and session policy_status remains PENDING
        detail = client.get(f"/api/v1/sessions/{session_id}").json()
        assert detail["agents"][0]["policy_status"] == "PENDING"

        # 2. ACK đúng Agent (PC01 ACKs its own command) -> 200 OK
        correct_ack = client.post(
            f"/api/v1/commands/{cmd_id}/acknowledge",
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC01",
            },
        )
        assert correct_ack.status_code == 200
        detail = client.get(f"/api/v1/sessions/{session_id}").json()
        assert detail["agents"][0]["policy_status"] == "APPLIED"

        # 3. ACK trùng (Duplicate ACK on already acknowledged command) -> 409 INVALID_STATE
        dup_ack = client.post(
            f"/api/v1/commands/{cmd_id}/acknowledge",
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC01",
            },
        )
        assert dup_ack.status_code == 409
        assert dup_ack.json()["code"] == "INVALID_STATE"
        assert "command is not awaiting acknowledgement" in dup_ack.json()["detail"]


def test_production_like_disables_direct_http_ack_for_all_roles(tmp_path: Path) -> None:
    app = create_app(tmp_path / "ack-auth-regression.db")
    app.state.settings = replace(app.state.settings, environment="production-like")
    key = "phase-9-http-auth-key-that-is-at-least-thirty-two-bytes"
    app.state.token_service = TokenService(key)

    app.state.container.register_agent(
        RegisterAgentInput("PC01", "HOST-PC01", "192.168.3.56", "1.1.0")
    )
    app.state.container.register_agent(
        RegisterAgentInput("PC02", "HOST-PC02", "192.168.3.57", "1.1.0")
    )

    with TestClient(app) as client:
        # Create session using admin token
        admin_principal = Principal("admin-user", Role.ADMIN, frozenset())
        admin_token = app.state.token_service.issue(admin_principal)
        created = client.post(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "PBL4 Final",
                "room": "A101",
                "agent_ids": ["PC01"],
                "policy_profile": "INTERNET_NO_AI",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]

        commands = client.get(
            "/api/v1/agents/PC01/commands",
            headers={"Authorization": f"Bearer {admin_token}"},
        ).json()
        cmd_id = commands[0]["id"]
        policy_hash = commands[0]["payload"]["policy_hash"]

        # In production-like, Direct HTTP ACK is disabled for all roles (including EXAMINER/ADMIN)
        pc02_token = app.state.token_service.issue(
            Principal("PC02", Role.EXAMINER, frozenset({session_id}))
        )
        examiner_ack = client.post(
            f"/api/v1/commands/{cmd_id}/acknowledge",
            headers={"Authorization": f"Bearer {pc02_token}"},
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC01",
            },
        )
        assert examiner_ack.status_code == 403
        assert "direct HTTP acknowledgement is disabled" in examiner_ack.json()["detail"]

        # Admin role also cannot acknowledge via HTTP in production-like
        admin_ack = client.post(
            f"/api/v1/commands/{cmd_id}/acknowledge",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC01",
            },
        )
        assert admin_ack.status_code == 403
        assert "direct HTTP acknowledgement is disabled" in admin_ack.json()["detail"]

        # Verify command remains PENDING and unacknowledged
        with app.state.container.database.unit_of_work() as uow:
            persisted = uow.commands.get(cmd_id)
        assert persisted.status == CommandStatus.PENDING
        assert persisted.acknowledged_at is None

        # Verify session state reflects PC01 is not compliant and policy is PENDING
        detail = client.get(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        ).json()
        assert detail["agents"][0]["policy_status"] == "PENDING"
        assert detail["workstations"]["PC01"]["policy_compliant"] is False
        assert detail["workstations"]["PC01"]["actual_policy_hash"] is None


def test_p0_03_get_agent_commands_is_read_only_and_lifecycle(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "p0_03.db")
    with TestClient(app) as client:
        _register(client, "PC01")
        session_res = client.post(
            "/api/v1/sessions",
            json={
                "name": "P0-03 Regression Session",
                "room": "ROOM-01",
                "agent_ids": ["PC01"],
                "policy_profile": "INTERNET_NO_AI",
            },
        )
        assert session_res.status_code == 201

        # 1. Simulate opening Command Queue Modal or refreshing it 5 consecutive times
        for _ in range(5):
            res = client.get("/api/v1/agents/PC01/commands")
            assert res.status_code == 200
            commands = res.json()
            assert len(commands) == 1
            cmd = commands[0]
            assert cmd["status"] == "PENDING"
            assert cmd["attempt_count"] == 0
            assert cmd["last_attempt_at"] is None
            assert cmd["next_retry_at"] is None

        # Verify database record remains untouched (strictly PENDING, 0 attempts)
        with app.state.container.database.unit_of_work() as uow:
            persisted = uow.commands.get(cmd["id"])
            assert persisted.status == CommandStatus.PENDING
            assert persisted.attempt_count == 0
            assert persisted.last_attempt_at is None
            assert persisted.next_retry_at is None

        # Attempting to acknowledge command directly while still PENDING must be rejected
        with pytest.raises(
            InvalidStateTransitionError,
            match="command is not awaiting acknowledgement",
        ):
            app.state.container.acknowledge_command(
                AcknowledgeCommandInput(
                    command_id=cmd["id"],
                    success=True,
                    policy_hash=cmd["payload"]["policy_hash"],
                    actor="PC01",
                )
            )

        # 2. Gateway dispatch retrieves and claims command for actual delivery
        dispatched = app.state.container.get_pending_commands("PC01")
        assert len(dispatched) == 1
        assert dispatched[0].status == CommandStatus.DELIVERED
        assert dispatched[0].attempt_count == 1
        assert dispatched[0].last_attempt_at is not None

        # 3. Viewing list after Gateway dispatch reflects DELIVERED without mutating attempts
        res_first = client.get("/api/v1/agents/PC01/commands")
        assert res_first.status_code == 200
        cmd_first = res_first.json()[0]
        assert cmd_first["status"] == "DELIVERED"
        assert cmd_first["attempt_count"] == 1
        assert cmd_first["last_attempt_at"] is not None
        assert cmd_first["next_retry_at"] is not None

        # Consecutive GETs must remain strictly idempotent
        for _ in range(3):
            res = client.get("/api/v1/agents/PC01/commands")
            assert res.status_code == 200
            cmd_after = res.json()[0]
            assert cmd_after["status"] == "DELIVERED"
            assert cmd_after["attempt_count"] == 1
            assert cmd_after["last_attempt_at"] == cmd_first["last_attempt_at"]
            assert cmd_after["next_retry_at"] == cmd_first["next_retry_at"]

        # 4. Agent acknowledges command -> transitions to ACKNOWLEDGED
        app.state.container.acknowledge_command(
            AcknowledgeCommandInput(
                command_id=cmd["id"],
                success=True,
                policy_hash=cmd["payload"]["policy_hash"],
                actor="PC01",
            )
        )

        with app.state.container.database.unit_of_work() as uow:
            persisted = uow.commands.get(cmd["id"])
            assert persisted.status == CommandStatus.ACKNOWLEDGED
            assert persisted.acknowledged_at is not None

        # Active commands list is now empty
        res_empty = client.get("/api/v1/agents/PC01/commands")
        assert res_empty.status_code == 200
        assert res_empty.json() == []

        # 5. Duplicate ACK is rejected
        with pytest.raises(InvalidStateTransitionError):
            app.state.container.acknowledge_command(
                AcknowledgeCommandInput(
                    command_id=cmd["id"],
                    success=True,
                    policy_hash=cmd["payload"]["policy_hash"],
                    actor="PC01",
                )
            )


def test_unauthorized_ack_does_not_mutate_pending_command(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "unauth_ack.db")
    with TestClient(app) as client:
        _register(client, "PC01")
        _register(client, "PC02")
        session_res = client.post(
            "/api/v1/sessions",
            json={
                "name": "Unauthorized ACK Test Session",
                "room": "ROOM-01",
                "agent_ids": ["PC01"],
                "policy_profile": "INTERNET_NO_AI",
            },
        )
        assert session_res.status_code == 201

        # Lấy danh sách command của PC01 (read-only, trạng thái vẫn là PENDING)
        res = client.get("/api/v1/agents/PC01/commands")
        assert res.status_code == 200
        cmd = res.json()[0]
        cmd_id = cmd["id"]
        policy_hash = cmd["payload"]["policy_hash"]
        assert cmd["status"] == "PENDING"
        assert cmd["attempt_count"] == 0

        # PC02 gửi ACK cho command PENDING của PC01
        ack_res = client.post(
            f"/api/v1/commands/{cmd_id}/acknowledge",
            json={
                "success": True,
                "policy_hash": policy_hash,
                "actor": "PC02",
            },
        )
        assert ack_res.status_code == 422
        assert "does not match actor PC02" in ack_res.json()["detail"]

        # QUAN TRỌNG: Kiểm tra database, command của PC01 KHÔNG được bị chuyển sang DELIVERED,
        # attempt_count KHÔNG được tăng, và last_attempt_at phải là None
        with app.state.container.database.unit_of_work() as uow:
            persisted = uow.commands.get(cmd_id)
            assert persisted.status == CommandStatus.PENDING
            assert persisted.attempt_count == 0
            assert persisted.last_attempt_at is None
            assert persisted.acknowledged_at is None
