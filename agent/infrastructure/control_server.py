from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from urllib.request import Request, urlopen

from agent.domain.identity import WorkstationIdentity


class AgentClient:
    def __init__(
        self,
        server_url: str,
        timeout_seconds: int = 5,
        opener: Callable[..., Any] = urlopen,
    ):
        self._server_url = server_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def register(self, identity: WorkstationIdentity) -> None:
        payload = asdict(identity)
        payload["id"] = payload.pop("agent_id")
        self._post("/api/v1/agents/register", payload)

    def heartbeat(self, agent_id: str) -> None:
        self._post(f"/api/v1/agents/{agent_id}/heartbeat", {})

    def pending_commands(self, agent_id: str) -> list[dict[str, Any]]:
        result = self._request("GET", f"/api/v1/agents/{agent_id}/commands")
        if not isinstance(result, list):
            raise OSError("control server returned an invalid command list")
        return result

    def active_policy(self, agent_id: str) -> tuple[str, dict[str, Any]] | None:
        result = self._request("GET", "/api/v1/sessions")
        if not isinstance(result, list):
            raise OSError("control server returned an invalid session list")
        for session in result:
            if not isinstance(session, dict) or session.get("status") != "RUNNING":
                continue
            agents = session.get("agents")
            policy = session.get("policy")
            if not isinstance(agents, list) or not isinstance(policy, dict):
                continue
            if any(
                isinstance(agent, dict) and agent.get("id") == agent_id
                for agent in agents
            ):
                session_id = session.get("id")
                if isinstance(session_id, str):
                    return session_id, policy
        return None

    def acknowledge_command(
        self,
        command_id: str,
        *,
        success: bool,
        policy_hash: str | None = None,
        error: str | None = None,
        actor: str,
    ) -> None:
        self._post(
            f"/api/v1/commands/{command_id}/acknowledge",
            {
                "success": success,
                "policy_hash": policy_hash,
                "error": error,
                "actor": actor,
            },
        )

    def report_policy_violation(
        self,
        session_id: str,
        agent_id: str,
        destination: str,
    ) -> None:
        self._post(
            f"/api/v1/sessions/{session_id}/telemetry",
            {
                "workstation_id": agent_id,
                "event_type": "POLICY_VIOLATION",
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "BLOCKED",
                "destination": destination,
                "payload": {"source": "agent-loopback-monitor"},
            },
        )

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        request = Request(
            f"{self._server_url}{path}",
            data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with self._opener(request, timeout=self._timeout_seconds) as response:
            body = response.read()
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OSError("control server returned invalid JSON") from exc
