"""Compatibility import; routing use cases depend on application ports."""

from apps.gateway.app.application.ports import BackendSender
from apps.gateway.app.application.routing import GatewayRouter

__all__ = ["GatewayRouter", "BackendSender"]
