from pathlib import Path

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
        assert apply_command["status"] == "DELIVERED"
        assert apply_command["attempt_count"] == 1
        assert apply_command["last_attempt_at"] is not None
        assert apply_command["next_retry_at"] is not None
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
