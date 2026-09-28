from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.exam_pipeline import TelemetryInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.security import Permission
from app.domain.entities.gateway import GatewayStatus
from app.domain.entities.operations import Command as DomainCommand
from app.domain.exceptions.errors import DomainError
from app.domain.value_objects.enums import Severity
from app.infrastructure.security import MachineCredentialRegistry
from app.presentation.api.deps import authorize
from contracts.v2 import (
    Ack,
    AckStatus,
    AgentHello,
    ApplyPolicyPayload,
    Command,
    CommandType,
    DeliveryFailure,
    ErrorCode,
    Event,
    EventReceipt,
    EventReceiptStatus,
    GatewayEnvelope,
    GatewayHealth,
    GatewayHello,
    GatewayMessageType,
    HealthCheckPayload,
    PolicyEnvelope,
    Presence,
    PresenceHealth,
    RestoreBaselinePayload,
    compute_command_authorization,
    compute_policy_signature,
)

router = APIRouter(prefix="/api/v2/gateways", tags=["gateways"])


@router.get("")
def list_gateways(request: Request) -> list[dict]:
    authorize(request, Permission.VIEW_AGENT)
    values = []
    for gateway in request.app.state.container.list_gateways():
        presence = request.app.state.presence.get_gateway(gateway.id) or {}
        values.append(
            {
            "gateway_id": gateway.id,
            "room_id": gateway.room_id,
            "status": gateway.status,
            "version": gateway.version,
            "last_seen": gateway.last_seen,
            "connected_agent_count": gateway.connected_agent_count,
            "backend_uplink_status": gateway.backend_uplink_status,
            "pending_event_count": presence.get("pending_event_count"),
            "oldest_pending_event_age": presence.get("oldest_pending_event_age"),
            "last_flush_success_at": presence.get("last_flush_success_at"),
            "last_flush_error": presence.get("last_flush_error"),
            "buffer_status": presence.get("buffer_status"),
            }
        )
    return values


@router.get("/agents/{agent_id}")
def resolve_agent_gateway(
    agent_id: str, request: Request
) -> dict | None:
    authorize(request, Permission.VIEW_AGENT)
    binding = request.app.state.container.resolve_gateway_for_agent(agent_id)
    if binding is None:
        return None
    return {
        "agent_id": binding.agent_id,
        "gateway_id": binding.gateway_id,
        "bound_at": binding.bound_at,
    }


@router.websocket("/ws/{gateway_id}")
async def gateway_uplink(websocket: WebSocket, gateway_id: str) -> None:
    container = websocket.app.state.container
    configured_token = websocket.app.state.settings.gateway_bootstrap_token
    supplied = websocket.headers.get("authorization", "")
    supplied_secret = supplied[7:] if supplied.startswith("Bearer ") else ""
    credential_config = websocket.app.state.settings.gateway_credentials_json
    credential_registry = MachineCredentialRegistry(credential_config or "{}")
    reason = (
        credential_registry.failure_reason(gateway_id, supplied_secret)
        if credential_config
        else None
        if configured_token and secrets.compare_digest(supplied_secret, configured_token)
        else "INVALID_CREDENTIAL"
    )
    valid = reason is None
    if not valid:
        container.security_audit.record(
            action="AUTHENTICATION_DENIED",
            actor=gateway_id,
            actor_type="GATEWAY",
            resource_type="GATEWAY_UPLINK",
            resource_id=gateway_id,
            reason_code=reason or "INVALID_CREDENTIAL",
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    registry = websocket.app.state.gateway_connections
    connected = False
    try:
        first = GatewayEnvelope.model_validate_json(await websocket.receive_text())
        if first.message_type != GatewayMessageType.GATEWAY_HELLO:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        hello = GatewayHello.model_validate(first.payload)
        if hello.gateway_id != gateway_id or first.source_id != gateway_id:
            container.security_audit.record(
                action="AUTHENTICATION_DENIED",
                actor=gateway_id,
                actor_type="GATEWAY",
                resource_type="GATEWAY_UPLINK",
                resource_id=gateway_id,
                reason_code="IDENTITY_MISMATCH",
                correlation_id=first.correlation_id,
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        container.register_gateway(
            RegisterGatewayInput(gateway_id, hello.room_id, hello.gateway_version)
        )
        await registry.connect(gateway_id, websocket)
        connected = True

        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
                envelope = GatewayEnvelope.model_validate_json(raw)
                response = await _handle_gateway_message(
                    gateway_id, envelope, container, websocket.app.state.presence
                )
                if response is not None:
                    await websocket.send_text(response.to_json())
            except TimeoutError:
                pass
            except (DomainError, ValidationError, ValueError):
                await websocket.send_text(
                    _error_envelope(gateway_id, "invalid Protocol v2 message").to_json()
                )
            await _route_pending_commands(gateway_id, container, registry)
    except (ValidationError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    except WebSocketDisconnect:
        pass
    finally:
        if connected:
            await registry.disconnect(gateway_id, websocket)
            container.update_gateway_health(gateway_id, online=False)


async def _handle_gateway_message(
    gateway_id: str, envelope: GatewayEnvelope, container, presence_store=None
) -> GatewayEnvelope | None:
    if envelope.message_type == GatewayMessageType.GATEWAY_HEALTH:
        health = GatewayHealth.model_validate(envelope.payload)
        if health.gateway_id != gateway_id or envelope.source_id != gateway_id:
            raise ValueError("gateway identity mismatch")
        container.update_gateway_health(
            gateway_id,
            connected_agent_count=health.connected_agent_count,
            backend_uplink_status=GatewayStatus(health.backend_uplink_status.value),
        )
        if presence_store is not None:
            presence_store.set_gateway(gateway_id, health.model_dump(mode="json"))
        return

    if envelope.message_type == GatewayMessageType.SECURITY_AUDIT:
        if envelope.source_id != gateway_id:
            raise ValueError("security audit source is not the authenticated Gateway")
        payload = envelope.payload
        container.security_audit.record(
            action="AUTHENTICATION_DENIED",
            actor=str(payload.get("actor_id", "unknown-agent")),
            actor_type="AGENT",
            resource_type="AGENT_SOCKET",
            resource_id=str(payload.get("actor_id", "unknown-agent")),
            reason_code=str(payload.get("reason_code", "INVALID_CREDENTIAL"))[:128],
            correlation_id=envelope.correlation_id,
        )
        return

    if envelope.message_type == GatewayMessageType.AGENT_HELLO:
        hello = AgentHello.model_validate(envelope.payload)
        if envelope.source_id != hello.agent_id:
            raise ValueError("agent identity mismatch")
        container.register_agent(
            RegisterAgentInput(
                hello.agent_id,
                hello.hostname,
                hello.ip_address,
                hello.agent_version,
            )
        )
        container.bind_agent_to_gateway(
            BindAgentToGatewayInput(hello.agent_id, gateway_id)
        )
        return

    binding = container.resolve_gateway_for_agent(envelope.source_id)
    if binding is None or binding.gateway_id != gateway_id:
        raise ValueError("agent is not bound to this gateway")

    if envelope.message_type == GatewayMessageType.PRESENCE:
        presence = Presence.model_validate(envelope.payload)
        if presence.agent_id != envelope.source_id:
            raise ValueError("presence identity mismatch")
        if presence.health == PresenceHealth.ONLINE:
            container.heartbeat_agent(presence.agent_id)
        if presence_store is not None:
            presence_store.set_agent(
                presence.agent_id, presence.model_dump(mode="json", exclude_none=True)
            )
        return

    if envelope.message_type == GatewayMessageType.ACK:
        ack = Ack.model_validate(envelope.payload)
        if envelope.correlation_id != ack.correlation_id:
            raise ValueError("ACK correlation does not match envelope")
        container.acknowledge_command(
            AcknowledgeCommandInput(
                command_id=ack.command_id,
                success=ack.status == AckStatus.SUCCEEDED,
                policy_hash=ack.applied_hash,
                error=ack.error_message,
                actor=envelope.source_id,
            )
        )
        return

    if envelope.message_type == GatewayMessageType.EVENT:
        event = Event.model_validate(envelope.payload)
        if event.agent_id != envelope.source_id:
            raise ValueError("event identity mismatch")
        details = dict(event.payload)
        if event.sequence is not None:
            details["sequence"] = event.sequence
        try:
            _stored, _incident_id, duplicate = (
                container.pipeline_service.ingest_protocol_event(
                    TelemetryInput(
                        session_id=event.session_id,
                        workstation_id=event.agent_id,
                        event_type=event.event_type.value,
                        severity=Severity(str(details.get("severity", "WARNING"))),
                        category=str(details.get("category", event.event_type.value)),
                        action=str(details.get("action", "OBSERVED")),
                        destination=(
                            str(details["destination"])
                            if details.get("destination") is not None
                            else None
                        ),
                        correlation_id=event.correlation_id,
                        payload=details,
                        event_id=event.event_id,
                        occurred_at=event.occurred_at,
                    )
                )
            )
        except DomainError as exc:
            return _event_receipt_envelope(
                gateway_id,
                event,
                EventReceiptStatus.REJECTED,
                error_code=ErrorCode.SESSION_MISMATCH,
                error_message=str(exc)[:500],
            )
        return _event_receipt_envelope(
            gateway_id,
            event,
            (
                EventReceiptStatus.ALREADY_PROCESSED
                if duplicate
                else EventReceiptStatus.ACCEPTED
            ),
        )

    if envelope.message_type == GatewayMessageType.DELIVERY_FAILURE:
        failure = DeliveryFailure.model_validate(envelope.payload)
        container.acknowledge_command(
            AcknowledgeCommandInput(
                command_id=failure.command_id,
                success=False,
                error=failure.reason,
                actor=gateway_id,
            )
        )
        return

    raise ValueError(f"unsupported gateway message: {envelope.message_type}")


async def _route_pending_commands(gateway_id: str, container, registry) -> None:
    for binding in container.list_agents_for_gateway(gateway_id):
        for domain_command in container.get_pending_commands(binding.agent_id):
            command = _to_contract_command(domain_command)
            envelope = GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.COMMAND,
                message_id=f"msg_{command.command_id}",
                correlation_id=command.correlation_id,
                source_id=gateway_id,
                target_id=command.target_id,
                payload=command.model_dump(mode="json", exclude_none=True),
            )
            await registry.send(gateway_id, envelope.to_json())


def _to_contract_command(command: DomainCommand) -> Command:
    operation = CommandType(command.type.value)
    if operation == CommandType.APPLY_POLICY:
        policy = PolicyEnvelope.from_legacy(
            policy_id=str(command.payload["profile"]),
            policy_version=int(command.payload["version"]),
            policy_hash=str(command.payload["policy_hash"]),
            session_id=command.session_id,
            issued_at=command.created_at,
            expires_at=command.expires_at,
            rules=dict(command.payload["rules"]),
        )
        signing_key = os.getenv("EECP_POLICY_SIGNING_KEY", "").strip()
        if signing_key:
            policy = policy.model_copy(
                update={
                    "signature": compute_policy_signature(
                        signing_key,
                        policy.policy_id,
                        policy.policy_version,
                        policy.rules,
                        policy.session_id,
                    )
                }
            )
        payload = ApplyPolicyPayload(policy=policy)
        policy_hash = policy.policy_hash
    elif operation == CommandType.RESTORE_BASELINE:
        payload = RestoreBaselinePayload()
        value = command.payload.get("policy_hash")
        policy_hash = value if isinstance(value, str) else None
    else:
        payload = HealthCheckPayload(nonce=command.id)
        policy_hash = None
    command_fields = {
        "protocol_version": 2,
        "command_id": command.id,
        "operation": operation,
        "session_id": command.session_id,
        "target_id": command.target_id,
        "policy_hash": policy_hash,
        "issued_at": command.created_at,
        "deadline": command.expires_at or command.created_at,
        "correlation_id": command.id,
    }
    command_signing_key = os.getenv("EECP_COMMAND_SIGNING_KEY", "").strip()
    authorization = (
        compute_command_authorization(command_signing_key, **command_fields)
        if command_signing_key and operation != CommandType.HEALTH_CHECK
        else None
    )
    return Command(
        protocol_version=2,
        command_id=command.id,
        command_type=operation,
        target_id=command.target_id,
        session_id=command.session_id,
        issued_at=command.created_at,
        deadline=command.expires_at or command.created_at,
        policy_hash=policy_hash,
        payload=payload,
        correlation_id=command.id,
        authorization=authorization,
    )


def _error_envelope(target_id: str, message: str) -> GatewayEnvelope:
    now = datetime.now(UTC)
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.ERROR,
        message_id=f"error_{int(now.timestamp() * 1000)}",
        correlation_id="gateway-error",
        source_id="backend",
        target_id=target_id,
        payload={"message": message},
    )


def _event_receipt_envelope(
    gateway_id: str,
    event: Event,
    receipt_status: EventReceiptStatus,
    *,
    error_code: ErrorCode | None = None,
    error_message: str | None = None,
) -> GatewayEnvelope:
    received_at = datetime.now(UTC)
    receipt = EventReceipt(
        protocol_version=2,
        event_id=event.event_id,
        status=receipt_status,
        received_at=received_at,
        error_code=error_code,
        error_message=error_message,
    )
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.EVENT_RECEIPT,
        message_id=f"receipt_{event.event_id}",
        correlation_id=event.correlation_id or event.event_id,
        source_id="backend",
        target_id=gateway_id,
        payload=receipt.model_dump(mode="json", exclude_none=True),
    )
