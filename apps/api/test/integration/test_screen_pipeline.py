import asyncio
import hashlib
import io
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.application.security import Principal, Role
from app.infrastructure.security import TokenService
from app.main import create_app
from app.presentation.api.routers.gateways import _handle_gateway_message
from fastapi.testclient import TestClient
from PIL import Image

from agent.client.screen.capture import CapturedFrame
from agent.client.screen.manager import EvidenceManager
from agent.client.screen.transport import ScreenUploader
from apps.api.test.integration.test_local_gateway_agent_ws import FakeUplink, _settings
from apps.api.test.integration.test_security_events_api import _running_session
from apps.gateway.app.main import create_app as create_gateway
from apps.gateway.app.screen import ScreenForwarder, ScreenSpool
from contracts.v2 import Event, EventType, GatewayEnvelope, GatewayMessageType
from contracts.v2.screen import ScreenFrame, ScreenRequest, ScreenResponse


def image_bytes():
    buffer = io.BytesIO()
    with Image.new("RGB", (320, 180), "navy") as image:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


def frame(session_id, **changes):
    data = image_bytes()
    now = datetime.now(UTC)
    return ScreenFrame.model_validate(
        {
            "protocol_version": 2,
            "frame_id": "frame1",
            "evidence_id": "evidence1",
            "agent_id": "PC01",
            "session_id": session_id,
            "event_id": "evt1",
            "captured_at": now,
            "event_occurred_at": now,
            "available_until": now + timedelta(minutes=15),
            "kind": "trigger",
            "width": 320,
            "height": 180,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            **changes,
        }
    )


def setup_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "gateway-token")
    app = create_app(tmp_path / "screen.db")
    client = TestClient(app)
    session_id = _running_session(client)
    app.state.container.register_gateway(RegisterGatewayInput("GW1", "LAB", "1"))
    app.state.container.bind_agent_to_gateway(BindAgentToGatewayInput("PC01", "GW1"))
    value = frame(session_id)
    result = client.post(
        f"/api/v1/sessions/{session_id}/telemetry",
        json={
            "workstation_id": "PC01",
            "event_type": "POLICY_VIOLATION",
            "severity": "WARNING",
            "category": "PROHIBITED_WEBSITE",
            "action": "OBSERVED",
            "event_id": "evt1",
            "occurred_at": value.event_occurred_at.isoformat(),
            "payload": {"evidence_id": "evidence1", "source": "windows-dns-cache"},
        },
    )
    assert result.status_code == 202, result.text
    return app, client, session_id, value


def ingest(client, value, data=None, token="gateway-token", gateway="GW1"):
    return client.post(
        f"/api/v2/screen/ingest/{gateway}",
        content=data or image_bytes(),
        headers={
            "Authorization": f"Bearer {token}",
            "X-EECP-Frame": value.to_json(),
            "Content-Type": value.content_type,
        },
    )


def test_pre_event_representative_is_bounded_and_does_not_enable_bulk_context_upload(
    tmp_path, monkeypatch
):
    _app, backend, _session_id, value = setup_backend(tmp_path, monkeypatch)
    historical = value.model_copy(update={
        "kind": "event_context", "frame_id": "before-event",
        "captured_at": value.event_occurred_at - timedelta(seconds=1),
        "event_received_at": value.event_occurred_at + timedelta(seconds=2),
    })
    response = ingest(backend, historical)
    assert response.status_code == 200, response.text
    assert ingest(backend, historical).json()["server_stored"]
    for index in range(3):
        extra = historical.model_copy(update={"frame_id": f"extra-before-{index}"})
        assert ingest(backend, extra).status_code == 200
    extra = historical.model_copy(update={"frame_id": "fifth-before"})
    assert ingest(backend, extra).status_code == 409
    assert ingest(backend, value).status_code == 200  # Separate post-detection trigger.
    second_trigger = value.model_copy(update={"frame_id": "second-trigger"})
    assert ingest(backend, second_trigger).status_code == 409
    values = backend.get(f"/api/v1/sessions/{value.session_id}/screen/evidence").json()
    assert len(values[0]["frames"]) == 5
    assert ingest(backend, historical).json()["server_stored"]  # Retry at the five-frame limit.
    assert ingest(backend, value.model_copy(update={"kind": "context"})).status_code == 403
    for offset in (-31, 1):
        invalid = historical.model_copy(update={
            "captured_at": historical.event_occurred_at + timedelta(seconds=offset),
        })
        assert ingest(backend, invalid).status_code == 422


def test_merged_evidence_lists_every_linked_event_without_replacing_primary(
    tmp_path, monkeypatch
):
    _app, backend, session_id, value = setup_backend(tmp_path, monkeypatch)
    assert ingest(backend, value).status_code == 200
    response = backend.post(f"/api/v1/sessions/{session_id}/telemetry", json={
        "workstation_id": "PC01", "event_type": "POLICY_VIOLATION",
        "severity": "WARNING", "category": "PROHIBITED_WEBSITE", "action": "OBSERVED",
        "event_id": "evt2", "occurred_at": value.event_occurred_at.isoformat(),
        "payload": {"evidence_id": "evidence1"},
    })
    assert response.status_code == 202, response.text
    values = backend.get(f"/api/v1/sessions/{session_id}/screen/evidence").json()
    assert values[0]["event_id"] == "evt1"
    assert set(values[0]["event_ids"]) == {"evt1", "evt2"}
    assert len(values[0]["frames"]) == 1


def test_gateway_reloads_old_manifests_without_optional_timing_field(tmp_path):
    value = frame("ses1")
    spool = ScreenSpool(tmp_path)
    spool.put(value, image_bytes())
    manifest = tmp_path / f"{spool.key(value)}.json"
    old = json.loads(manifest.read_text())
    old["frame"].pop("event_received_at", None)
    manifest.write_text(json.dumps(old))
    restarted = ScreenSpool(tmp_path)
    receipt = restarted.put(value, image_bytes())
    assert receipt["gateway_received"] and not receipt["server_stored"]


@pytest.mark.parametrize(("kind", "limit"), [("trigger", 1), ("event_context", 4)])
def test_storage_atomically_limits_concurrent_automatic_representatives(
    tmp_path, monkeypatch, kind, limit
):
    from concurrent.futures import ThreadPoolExecutor

    app, _backend, _session_id, value = setup_backend(tmp_path, monkeypatch)

    def store(index):
        candidate = value.model_copy(update={"frame_id": f"concurrent-{index}", "kind": kind})
        try:
            return app.state.screen_store.store_frame(candidate, image_bytes())["server_stored"]
        except ValueError as exc:
            assert "representative" in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(store, range(8)))
    assert results.count(True) == limit
    assert len(app.state.screen_store.frames(value.evidence_id)) == limit


def test_session_finish_during_wan_outage_drains_existing_images_after_recovery(
    tmp_path, monkeypatch
):
    _app, backend, session_id, value = setup_backend(tmp_path, monkeypatch)
    spool = ScreenSpool(tmp_path / "gateway")
    online = [False]
    captures = []
    now = value.event_occurred_at

    def capture(high):
        captures.append(high)
        return CapturedFrame(f"captured-{len(captures)}", now, image_bytes(), 320, 180)

    def upload(metadata, data):
        receipt = spool.put(metadata, data)
        if online[0] and not receipt["server_stored"]:
            response = ingest(backend, metadata, data)
            assert response.status_code == 200, response.text
            spool.complete(spool.key(metadata), response.json())
        return spool.put(metadata, data)["server_stored"]

    manager = EvidenceManager("PC01", lambda _: None, upload, capture=capture, clock=lambda: now)
    manager.activate(session_id, {})
    manager.ring.add(CapturedFrame("before", now - timedelta(seconds=1), image_bytes(), 320, 180))
    evidence_id = manager.on_event(Event(
        protocol_version=2, event_id="evt-drain", session_id=session_id, agent_id="PC01",
        event_type=EventType.POLICY_VIOLATION, occurred_at=now,
        payload={"destination": "chatgpt.com", "severity": "WARNING"},
    ))
    response = backend.post(f"/api/v1/sessions/{session_id}/telemetry", json={
        "workstation_id": "PC01", "event_type": "POLICY_VIOLATION",
        "severity": "WARNING", "category": "PROHIBITED_WEBSITE", "action": "OBSERVED",
        "event_id": "evt-drain", "occurred_at": now.isoformat(),
        "payload": {"evidence_id": evidence_id},
    })
    assert response.status_code == 202, response.text
    manager.capture_once()
    assert manager.upload_once()
    assert spool.health()["pending_frames"] == 1
    response = backend.patch(f"/api/v1/sessions/{session_id}/status", json={"status": "FINISHED"})
    assert response.status_code == 200, response.text
    manager.deactivate()
    capture_count = len(captures)
    manager.capture_once()
    assert len(captures) == capture_count
    online[0] = True
    for job in manager._jobs.values():
        job.next_attempt = 0
    while manager.upload_once():
        pass
    assert not manager._jobs and manager.evidence_bytes == 0
    values = backend.get(f"/api/v1/sessions/{session_id}/screen/evidence").json()
    stored = next(item for item in values if item["id"] == evidence_id)
    assert not stored["replay_available"]
    assert {item["kind"] for item in stored["frames"]} == {"event_context", "trigger"}


def test_violation_gateway_durable_ack_server_storage_and_view_after_client_exit(
    tmp_path, monkeypatch
):
    app, backend, session_id, value = setup_backend(tmp_path, monkeypatch)
    spool = ScreenSpool(tmp_path / "gateway")
    receipt = spool.put(value, image_bytes())
    assert receipt["gateway_received"] and not receipt["server_stored"]
    restarted = ScreenSpool(tmp_path / "gateway")
    assert restarted.health()["pending_frames"] == 1
    response = ingest(backend, value)
    assert response.status_code == 200, response.text
    restarted.complete(restarted.key(value), response.json())
    assert not list((tmp_path / "gateway").glob("*.bin"))
    assert restarted.put(value, image_bytes())["server_stored"]
    # A lost receipt followed by a retry does not create duplicate records/files.
    assert ingest(backend, value).json()["server_stored"]
    items = backend.get(f"/api/v1/sessions/{session_id}/screen/evidence").json()
    assert len(items) == 1 and len(items[0]["frames"]) == 1
    assert len(list(app.state.screen_store.root.glob("*.bin"))) == 1
    # No Agent/Gateway socket is needed for later reads, including after the exam.
    assert (
        backend.patch(
            f"/api/v1/sessions/{session_id}/status", json={"status": "FINISHED"}
        ).status_code
        == 200
    )
    downloaded = backend.get(items[0]["frames"][0]["url"])
    assert downloaded.content == image_bytes()
    assert downloaded.headers["cache-control"] == "private, no-store"


def test_checksum_dimension_identity_and_context_authorization_rejects(tmp_path, monkeypatch):
    _, client, _, value = setup_backend(tmp_path, monkeypatch)
    assert ingest(client, value, token="wrong").status_code == 401
    assert ingest(client, value, gateway="other").status_code == 403
    assert ingest(client, value, data=b"wrong").status_code == 409
    assert ingest(client, value.model_copy(update={"width": 321})).status_code == 409
    assert ingest(client, value.model_copy(update={"kind": "context"})).status_code == 403
    assert ingest(client, value.model_copy(update={"evidence_id": "forged"})).status_code == 403
    assert ingest(client, value.model_copy(update={"agent_id": "other"})).status_code == 403


def test_snapshot_request_is_routed_manifest_bound_and_scoped(tmp_path, monkeypatch):
    app, client, session_id, _ = setup_backend(tmp_path, monkeypatch)

    class Socket:
        messages = []

        async def send_text(self, text):
            self.messages.append(GatewayEnvelope.model_validate_json(text))

    socket = Socket()
    asyncio.run(app.state.gateway_connections.connect("GW1", socket))
    response = client.post(f"/api/v1/sessions/{session_id}/agents/PC01/screen/requests", json={})
    assert response.status_code == 202, response.text
    control = ScreenRequest.model_validate(socket.messages[0].payload)
    assert socket.messages[0].message_type == GatewayMessageType.SCREEN_REQUEST
    value = frame(
        session_id,
        event_id=None,
        event_occurred_at=None,
        kind="snapshot",
        evidence_id="snapshot1",
        request_id=control.request_id,
    )
    assert ingest(client, value).status_code == 409  # Manifest must arrive before image.
    notice = ScreenResponse(
        protocol_version=2,
        request_id=control.request_id,
        agent_id="PC01",
        session_id=session_id,
        evidence_id="snapshot1",
        status="uploading",
        frames=[value],
        available_until=value.available_until,
    )
    envelope = GatewayEnvelope(
        protocol_version=2,
        message_type="SCREEN_RESPONSE",
        message_id=control.request_id,
        correlation_id=control.request_id,
        source_id="PC01",
        target_id="backend",
        payload=notice.model_dump(mode="json"),
    )
    asyncio.run(
        _handle_gateway_message(
            "GW1", envelope, app.state.container, screen_store=app.state.screen_store
        )
    )
    assert ingest(client, value).status_code == 200
    assert (
        client.get(f"/api/v1/screen/requests/{control.request_id}").json()["status"] == "available"
    )
    with pytest.raises(ValueError):
        asyncio.run(
            _handle_gateway_message(
                "other", envelope, app.state.container, screen_store=app.state.screen_store
            )
        )


def test_replay_uploads_selected_context_and_preserves_representative(tmp_path, monkeypatch):
    app, client, session_id, trigger = setup_backend(tmp_path, monkeypatch)
    assert ingest(client, trigger).status_code == 200
    now = datetime.now(UTC)
    control = ScreenRequest(
        protocol_version=2,
        request_id="replay1",
        agent_id="PC01",
        session_id=session_id,
        mode="REPLAY",
        evidence_id=trigger.evidence_id,
        issued_at=now,
        deadline=now + timedelta(seconds=60),
        max_frames=2,
    )
    app.state.screen_store.save_request(control)
    context = trigger.model_copy(
        update={"frame_id": "context1", "kind": "context", "request_id": control.request_id}
    )
    app.state.screen_store.save_response(
        ScreenResponse(
            protocol_version=2,
            request_id=control.request_id,
            agent_id="PC01",
            session_id=session_id,
            evidence_id=trigger.evidence_id,
            event_id=trigger.event_id,
            status="uploading",
            frames=[context],
            available_until=trigger.available_until,
        )
    )
    assert ingest(client, context).status_code == 200
    forged = context.model_copy(update={"frame_id": "not_selected"})
    assert ingest(client, forged).status_code == 403
    values = app.state.screen_store.frames(trigger.evidence_id)
    assert {value["kind"] for value in values} == {"context", "trigger"}
    assert len(list(app.state.screen_store.root.glob("*.bin"))) == 1


def test_gateway_full_expiry_and_bad_ack_never_report_server_stored(tmp_path):
    value = frame("ses1")
    spool = ScreenSpool(tmp_path, max_bytes=1)
    with pytest.raises(OSError, match="quota"):
        spool.put(value, image_bytes())
    spool = ScreenSpool(tmp_path)
    spool.put(value, image_bytes())
    key = spool.key(value)
    with pytest.raises(ValueError, match="acknowledge"):
        spool.complete(key, {"server_stored": True, "sha256": "0" * 64})
    assert (tmp_path / f"{key}.bin").exists()
    path = tmp_path / f"{key}.json"
    manifest = json.loads(path.read_text())
    manifest["expires_at"] = time.time() - 1
    path.write_text(json.dumps(manifest))
    spool = ScreenSpool(tmp_path)  # Reload durable state as on process restart.
    spool.cleanup()
    assert not spool.put(value, image_bytes())["server_stored"]
    assert spool.health()["expired_frames"] == 1


def test_gateway_http_upload_checks_agent_credentials_and_checksum(tmp_path):
    token_hash = hashlib.sha256(b"pc01-token").hexdigest()
    settings = replace(
        _settings(),
        screen_spool_path=str(tmp_path),
        agent_credentials_json=json.dumps(
            {
                "PC01": {"secret_sha256": token_hash},
            }
        ),
    )
    gateway = create_gateway(settings, FakeUplink())
    client = TestClient(gateway)
    value = frame("ses1")
    headers = {"Authorization": "Bearer pc01-token", "X-EECP-Frame": value.to_json()}
    result = client.post("/screen/frames", content=image_bytes(), headers=headers)
    assert result.status_code == 202 and not result.json()["server_stored"]
    headers["X-EECP-Frame"] = value.model_copy(update={"agent_id": "PC02"}).to_json()
    assert client.post("/screen/frames", content=image_bytes(), headers=headers).status_code == 403


def test_server_expiry_removes_file_and_access_requires_examiner_scope(tmp_path, monkeypatch):
    app, client, session_id, value = setup_backend(tmp_path, monkeypatch)
    assert ingest(client, value).status_code == 200
    frame_url = app.state.screen_store.frames(value.evidence_id)[0]["url"]
    app.state.settings = replace(app.state.settings, environment="production-like")
    app.state.token_service = TokenService("long-screen-test-authentication-key")
    assert client.get(frame_url).status_code == 401
    outsider = app.state.token_service.issue(
        Principal("outsider", Role.EXAMINER, frozenset({"other"}))
    )
    assert client.get(frame_url, headers={"Authorization": f"Bearer {outsider}"}).status_code == 403
    token = app.state.token_service.issue(
        Principal("examiner", Role.EXAMINER, frozenset({session_id}))
    )
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(frame_url, headers=headers).status_code == 200
    with app.state.screen_store.connection() as connection:
        connection.execute(
            "UPDATE screen_frames SET expires_at=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    app.state.screen_store.expire()
    assert not list(app.state.screen_store.root.glob("*.bin"))
    assert client.get(frame_url, headers=headers).status_code == 410


def test_offline_gateway_request_returns_unavailable_without_losing_stored_evidence(
    tmp_path, monkeypatch
):
    _, client, session_id, value = setup_backend(tmp_path, monkeypatch)
    assert ingest(client, value).status_code == 200
    result = client.post(f"/api/v1/sessions/{session_id}/agents/PC01/screen/requests", json={})
    assert result.status_code == 202
    assert result.json()["status"] == "unavailable"
    assert client.get(f"/api/v1/sessions/{session_id}/screen/evidence").json()[0]["frames"]


def test_binary_upload_workers_retry_wan_failure_and_return_separate_durable_receipts(
    tmp_path, monkeypatch
):
    import urllib.error

    _, backend, _, value = setup_backend(tmp_path, monkeypatch)
    settings = replace(_settings(), gateway_id="GW1", screen_spool_path=str(tmp_path / "gateway"))
    app = create_gateway(settings, FakeUplink())
    gateway = TestClient(app)
    wan_online = [False]

    def bridge(http_request, **_options):
        if "/screen/frames" in http_request.full_url:
            response = gateway.post(
                "/screen/frames",
                content=http_request.data,
                headers=dict(http_request.header_items()),
            )
        else:
            if not wan_online[0]:
                raise urllib.error.URLError("WAN unavailable")
            response = backend.post(
                "/api/v2/screen/ingest/GW1",
                content=http_request.data,
                headers=dict(http_request.header_items()),
            )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                http_request.full_url,
                response.status_code,
                response.text,
                {},
                io.BytesIO(response.content),
            )
        return io.BytesIO(response.content)

    monkeypatch.setattr("urllib.request.urlopen", bridge)
    uploader = ScreenUploader("ws://gateway/ws/agents", "agent-token")
    assert not uploader(value, image_bytes())  # Gateway receipt is not a server receipt.
    spool = app.state.screen_spool
    forwarder = ScreenForwarder(settings, spool)
    key, manifest = spool.records()[0]
    forwarder.forward(key, manifest)
    assert spool.records()[0][1]["attempts"] == 1
    assert spool.health()["pending_frames"] == 1
    wan_online[0] = True
    forwarder.forward(key, spool.records()[0][1])
    assert spool.health()["pending_frames"] == 0
    assert uploader(value, image_bytes())
    assert not list((tmp_path / "gateway").glob("*.bin"))
    assert backend.app.state.screen_store.frames(value.evidence_id)


def test_offline_replay_request_does_not_destroy_existing_evidence_identity(tmp_path, monkeypatch):
    app, client, session_id, value = setup_backend(tmp_path, monkeypatch)
    assert ingest(client, value).status_code == 200
    result = client.post(
        f"/api/v1/sessions/{session_id}/agents/PC01/screen/requests",
        json={
            "mode": "REPLAY",
            "evidence_id": value.evidence_id,
        },
    )
    assert result.status_code == 202, result.text
    assert result.json()["status"] == "unavailable"
    stored = app.state.screen_store.evidence(value.evidence_id)
    assert stored["event_id"] == value.event_id
    assert stored["replay_available"]


def test_presence_synchronizes_capture_start_stop_without_clearing_policy_enforcement(
    tmp_path, monkeypatch
):
    from contracts.v2 import Presence

    app, client, session_id, _ = setup_backend(tmp_path, monkeypatch)
    presence = Presence(
        protocol_version=2,
        agent_id="PC01",
        last_seen=datetime.now(UTC),
        health="ONLINE",
        screen_capture_supported=True,
    )
    envelope = GatewayEnvelope(
        protocol_version=2,
        message_type="PRESENCE",
        message_id="presence1",
        correlation_id="presence1",
        source_id="PC01",
        target_id="backend",
        payload=presence.model_dump(mode="json"),
    )
    response = asyncio.run(_handle_gateway_message("GW1", envelope, app.state.container))
    assert response.message_type == "SCREEN_SESSION_STATE"
    assert response.payload["session_id"] == session_id
    assert (
        client.patch(
            f"/api/v1/sessions/{session_id}/status", json={"status": "FINISHED"}
        ).status_code
        == 200
    )
    response = asyncio.run(_handle_gateway_message("GW1", envelope, app.state.container))
    assert response.payload.get("session_id") is None
    assert response.message_type != "COMMAND"  # Enforcement is still owned by Service commands.


def test_backend_storage_quota_rejects_without_false_durable_ack(tmp_path, monkeypatch):
    app, client, _, value = setup_backend(tmp_path, monkeypatch)
    app.state.screen_store.max_bytes = 1
    response = ingest(client, value)
    assert response.status_code == 507
    assert not app.state.screen_store.frames(value.evidence_id)
    assert not list(app.state.screen_store.root.glob("*.bin"))


def test_invalid_encoded_image_with_valid_checksum_is_rejected(tmp_path, monkeypatch):
    _, client, _, value = setup_backend(tmp_path, monkeypatch)
    data = b"this is not an image"
    invalid = value.model_copy(
        update={"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    )
    assert ingest(client, invalid, data=data).status_code == 409


def test_thirty_agent_evidence_uploads_do_not_block_heartbeat(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "gateway-token")
    app = create_app(tmp_path / "many-agents.db")
    with TestClient(app) as client:
        agents = [f"PC{index:02d}" for index in range(1, 31)]
        app.state.container.register_gateway(RegisterGatewayInput("GW1", "LAB", "1"))
        for index, agent_id in enumerate(agents, 1):
            assert (
                client.post(
                    "/api/v1/agents/register",
                    json={
                        "id": agent_id,
                        "hostname": agent_id,
                        "ip_address": f"192.0.2.{index}",
                        "agent_version": "1",
                    },
                ).status_code
                == 201
            )
            app.state.container.bind_agent_to_gateway(BindAgentToGatewayInput(agent_id, "GW1"))
        created = client.post(
            "/api/v1/sessions",
            json={
                "name": "Thirty-Agent Evidence Test",
                "room": "LAB",
                "agent_ids": agents,
            },
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        for agent_id in agents:
            command = client.get(f"/api/v1/agents/{agent_id}/commands").json()[0]
            assert (
                client.post(
                    f"/api/v1/commands/{command['id']}/acknowledge",
                    json={
                        "success": True,
                        "policy_hash": command["payload"]["policy_hash"],
                        "actor": agent_id,
                    },
                ).status_code
                == 200
            )
        for state in ("READY", "RUNNING"):
            assert (
                client.patch(
                    f"/api/v1/sessions/{session_id}/status",
                    json={
                        "status": state,
                    },
                ).status_code
                == 200
            )
        frames = []
        for index, agent_id in enumerate(agents):
            value = frame(
                session_id,
                agent_id=agent_id,
                event_id=f"event_{index}",
                evidence_id=f"evidence_{index}",
                frame_id=f"frame_{index}",
            )
            response = client.post(
                f"/api/v1/sessions/{session_id}/telemetry",
                json={
                    "workstation_id": agent_id,
                    "event_type": "POLICY_VIOLATION",
                    "severity": "WARNING",
                    "category": "PROHIBITED_WEBSITE",
                    "action": "OBSERVED",
                    "event_id": value.event_id,
                    "occurred_at": value.event_occurred_at.isoformat(),
                    "payload": {"evidence_id": value.evidence_id},
                },
            )
            assert response.status_code == 202, response.text
            frames.append(value)
        with (
            ThreadPoolExecutor(max_workers=4) as uploads,
            ThreadPoolExecutor(max_workers=1) as control,
        ):
            pending = [uploads.submit(ingest, client, value) for value in frames]
            heartbeat = control.submit(client.post, "/api/v1/agents/PC01/heartbeat")
            assert heartbeat.result(timeout=10).status_code == 200
            results = [future.result(timeout=10) for future in pending]
        assert all(response.status_code == 200 for response in results)
        values = app.state.screen_store.list_evidence(session_id)
        assert {value["agent_id"] for value in values} == set(agents)


def test_screen_store_avoids_nested_connections_n_plus_one_and_read_lock(tmp_path, monkeypatch):
    app, client, session_id, first = setup_backend(tmp_path, monkeypatch)
    assert ingest(client, first).status_code == 200
    second = frame(
        session_id,
        frame_id="frame2",
        evidence_id="evidence2",
        event_id="evt2",
    )
    assert (
        client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "POLICY_VIOLATION",
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "OBSERVED",
                "event_id": "evt2",
                "occurred_at": second.event_occurred_at.isoformat(),
                "payload": {"evidence_id": "evidence2", "source": "windows-dns-cache"},
            },
        ).status_code
        == 202
    )
    assert ingest(client, second).status_code == 200

    now = datetime.now(UTC)
    control = ScreenRequest(
        protocol_version=2,
        request_id="req-single-conn",
        agent_id="PC01",
        session_id=session_id,
        mode="REPLAY",
        evidence_id=first.evidence_id,
        issued_at=now,
        deadline=now + timedelta(seconds=60),
    )
    store = app.state.screen_store
    store.save_request(control)
    store.save_response(
        ScreenResponse(
            protocol_version=2,
            request_id=control.request_id,
            agent_id="PC01",
            session_id=session_id,
            evidence_id=first.evidence_id,
            event_id=first.event_id,
            status="uploading",
            frames=[first],
            available_until=first.available_until,
        )
    )

    real_db = store.database
    active = 0
    queries: list[str] = []

    class SingleConnWrapper:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            queries.append(sql)
            return self._inner.execute(sql, params)

        def commit(self):
            return self._inner.commit()

        def close(self):
            nonlocal active
            active -= 1
            return self._inner.close()

    def checked_connect():
        nonlocal active
        if active >= 1:
            raise RuntimeError("nested database connection detected")
        active += 1
        return SingleConnWrapper(real_db.connect())

    class SinglePoolDatabase:
        def connect(self):
            return checked_connect()

    store.database = SinglePoolDatabase()
    try:
        items = store.list_evidence(session_id)
        assert len(items) == 2
        assert all(item["frames"] for item in items)
        assert sum("FROM screen_frames" in q for q in queries) == 1
        assert store.evidence(first.evidence_id) is not None
        assert store.get_request(control.request_id)["status"] == "available"
    finally:
        store.database = real_db

    # read_frame should not require acquiring store.lock on disk reads
    with store.lock:
        import threading

        result: list = []
        t = threading.Thread(
            target=lambda: result.append(store.read_frame(first.evidence_id, first.frame_id))
        )
        t.start()
        t.join(timeout=2)
        assert not t.is_alive()
        assert result and result[0] is not None


def test_list_evidence_throttles_audits_skips_full_telemetry_scan_and_publishes_realtime(
    tmp_path, monkeypatch
):
    app, client, session_id, value = setup_backend(tmp_path, monkeypatch)
    published = []

    async def fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(app.state.realtime_hub, "publish_change", fake_publish)
    assert ingest(client, value).status_code == 200
    assert any(
        item["resource"] == "sessions" and item["resource_id"] == session_id
        for item in published
    )
    assert (
        client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "POLICY_VIOLATION",
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "OBSERVED",
                "event_id": "evt_extra",
                "occurred_at": value.event_occurred_at.isoformat(),
                "payload": {"evidence_id": value.evidence_id},
            },
        ).status_code
        == 202
    )
    for _ in range(5):
        resp = client.get(f"/api/v1/sessions/{session_id}/screen/evidence")
        assert resp.status_code == 200
        assert set(resp.json()[0]["event_ids"]) == {"evt1", "evt_extra"}

    with app.state.container.database.unit_of_work() as uow:
        list_audits = [
            entry
            for entry in uow.audits.list_for_session(session_id)
            if entry.action == "SCREEN_EVIDENCE_LISTED"
        ]
    assert len(list_audits) == 1


def test_ingest_concurrency_env_and_clock_skew_rejection(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EECP_SCREEN_INGEST_CONCURRENCY", "12")
    app, client, session_id, value = setup_backend(tmp_path, monkeypatch)
    assert app.state.screen_ingest_slots._value == 12

    future = value.model_copy(
        update={
            "captured_at": datetime.now(UTC) + timedelta(seconds=15),
            "available_until": datetime.now(UTC) + timedelta(minutes=20),
            "kind": "trigger",
        }
    )
    with caplog.at_level("WARNING"):
        resp = ingest(client, future)
    assert resp.status_code == 409
    assert "NTP time sync" in resp.text
    assert any("CLOCK_SKEW_REJECTED" in rec.message for rec in caplog.records)
