from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegisterGatewayInput:
    gateway_id: str
    room_id: str
    version: str


@dataclass(frozen=True, slots=True)
class BindAgentToGatewayInput:
    agent_id: str
    gateway_id: str


@dataclass(frozen=True, slots=True)
class GatewayDeliveryFailureInput:
    gateway_id: str
    agent_id: str
    command_id: str
    correlation_id: str
    reason: str
