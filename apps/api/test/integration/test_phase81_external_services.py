from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.exam_pipeline import (
    CreateSessionInput,
    DeployPolicyInput,
    StartSessionInput,
    SubmitPreflightInput,
    TelemetryInput,
)
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.use_cases.agents.management import RegisterAgent
from app.application.use_cases.exam_sessions.pipeline import ExamPipelineService
from app.application.use_cases.gateways.management import (
    BindAgentToGateway,
    RegisterGateway,
    ResolveGatewayForAgent,
)
from app.application.use_cases.policies.management import (
    AcknowledgeCommand,
    GetPendingCommands,
)
from app.domain.entities.exam_session import PreflightCheck
from app.domain.value_objects.enums import Severity
from app.infrastructure.persistence.postgres import PostgresDatabase
from app.infrastructure.presence import RedisPresenceStore
from psycopg.errors import ForeignKeyViolation, UniqueViolation


def _required_url(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for real external-service validation")
    return value


def test_real_postgresql_migration_repository_restart_dedupe_and_constraints() -> None:
    database_url = _required_url("EECP_TEST_POSTGRES_URL")
    database = PostgresDatabase(database_url)
    database.migrate()
    database.migrate()
    database.require_schema()
    suffix = uuid4().hex[:10]
    agent_id = f"AGT-PG-{suffix}"
    gateway_id = f"GW-PG-{suffix}"

    RegisterAgent(database.unit_of_work)(
        RegisterAgentInput(agent_id, f"HOST-{suffix}", "192.0.2.20", "2.0")
    )
    RegisterGateway(database.unit_of_work)(RegisterGatewayInput(gateway_id, "LAB-PG", "2.0"))
    BindAgentToGateway(database.unit_of_work)(BindAgentToGatewayInput(agent_id, gateway_id))
    assert ResolveGatewayForAgent(database.unit_of_work)(agent_id).gateway_id == gateway_id

    pipeline = ExamPipelineService(database.unit_of_work)
    session = pipeline.create_session(
        CreateSessionInput("PostgreSQL validation", "LAB-PG", gateway_id, [agent_id], "test")
    )
    pipeline.deploy_policy(DeployPolicyInput(session.id, "LOCKDOWN", {"network": {}}, "test"))
    for target_id in (gateway_id, agent_id):
        command = GetPendingCommands(database.unit_of_work)(target_id)[0]
        AcknowledgeCommand(database.unit_of_work)(
            AcknowledgeCommandInput(
                command_id=command.id,
                success=True,
                policy_hash=command.payload["policy_hash"],
                actor=target_id,
            )
        )
    pipeline.submit_preflight(
        SubmitPreflightInput(
            session.id,
            agent_id,
            [PreflightCheck("external-postgres", True)],
        )
    )
    pipeline.start_session(StartSessionInput(session.id, "test"))
    telemetry = TelemetryInput(
        session_id=session.id,
        workstation_id=agent_id,
        event_type="POLICY_VIOLATION",
        severity=Severity.CRITICAL,
        category="POSTGRES_INTEGRATION",
        action="BLOCKED",
        event_id=f"EVT-PG-{suffix}",
    )
    first, incident_id, duplicate = pipeline.ingest_protocol_event(telemetry)
    assert not duplicate
    assert incident_id is not None

    restarted = PostgresDatabase(database_url)
    restarted.require_schema()
    second, duplicate_incident, duplicate = ExamPipelineService(
        restarted.unit_of_work
    ).ingest_protocol_event(telemetry)
    assert duplicate
    assert duplicate_incident is None
    assert second.id == first.id
    assert ExamPipelineService(restarted.unit_of_work).get_session(session.id).id == session.id
    with restarted.unit_of_work() as uow:
        assert uow.audits.verify_chain(session.id)
        assert uow.incidents.list_for_session(session.id)[0].id == incident_id

    connection = restarted.connect()
    try:
        with pytest.raises(UniqueViolation):
            connection.execute(
                "INSERT INTO agents(id, hostname, ip_address, status, agent_version, "
                "last_seen, created_at) SELECT id, hostname, ip_address, status, "
                "agent_version, last_seen, created_at FROM agents WHERE id = ?",
                (agent_id,),
            )
        connection.rollback()
        with pytest.raises(ForeignKeyViolation):
            connection.execute(
                "INSERT INTO agent_gateway_bindings(agent_id, gateway_id, bound_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (f"MISSING-{suffix}", gateway_id),
            )
        connection.rollback()
    finally:
        connection.close()
        restarted.close()
        database.close()


def test_real_redis_ttl_pubsub_expiry_loss_and_reannounce() -> None:
    redis_url = _required_url("EECP_TEST_REDIS_URL")
    store = RedisPresenceStore(redis_url, ttl_seconds=2)
    store._redis.flushdb()
    subscriber = store._redis.pubsub()
    subscriber.subscribe("presence:changed")
    subscriber.get_message(timeout=1)
    try:
        store.set_agent("AGT-REDIS", {"health": "ONLINE"})
        assert store.get_agent("AGT-REDIS")["health"] == "ONLINE"
        assert 0 < store._redis.ttl("presence:agent:AGT-REDIS") <= 2
        message = subscriber.get_message(timeout=2)
        assert message is not None and message["type"] == "message"

        time.sleep(1)
        store.set_agent("AGT-REDIS", {"health": "DEGRADED"})
        assert store._redis.ttl("presence:agent:AGT-REDIS") > 0
        time.sleep(2.1)
        assert store.get_agent("AGT-REDIS") is None

        store.set_gateway("GW-REDIS", {"buffer_status": "ONLINE"})
        assert store.get_gateway("GW-REDIS")["buffer_status"] == "ONLINE"
        store._redis.flushdb()
        assert store.get_gateway("GW-REDIS") is None
        store.set_gateway("GW-REDIS", {"buffer_status": "DEGRADED"})
        assert store.get_gateway("GW-REDIS")["buffer_status"] == "DEGRADED"
        assert store.health()
    finally:
        subscriber.close()
        store._redis.flushdb()

    unavailable = RedisPresenceStore("redis://127.0.0.1:1/0", ttl_seconds=2)
    assert not unavailable.health()
