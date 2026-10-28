# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import json
import math
import socket
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from apps.gateway.app.config import GatewaySettings
from apps.gateway.app.event_buffer import EventBuffer
from apps.gateway.app.main import create_app
from contracts.v2 import (
    Ack,
    AckStatus,
    AgentHello,
    Command,
    CommandType,
    Event,
    EventReceipt,
    EventReceiptStatus,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    HealthCheckPayload,
    Presence,
    PresenceHealth,
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    return round(value, 3)


def _latencies(values: list[float]) -> dict[str, float | None]:
    return {
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
    }


async def _wait_until(predicate, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(f"timed out waiting for {label}")


@dataclass(slots=True)
class Result:
    scenario: str
    agents: int
    duration_seconds: float
    successful_connections: int = 0
    failed_connections: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    commands_sent: int = 0
    acks_received: int = 0
    events_sent: int = 0
    events_accepted: int = 0
    event_loss: int = 0
    logical_duplicates: int = 0
    wrong_target_count: int = 0
    peak_backlog: int = 0
    flush_seconds: float | None = None
    throughput_per_second: float = 0.0
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    gateway_python_peak_mib: float | None = None
    queue_saturation_count: int = 0
    notes: list[str] = field(default_factory=list)


class FakeBackend:
    def __init__(self, gateway_id: str):
        self.gateway_id = gateway_id
        self.connection: ServerConnection | None = None
        self.connected = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.agent_hellos: dict[str, float] = {}
        self.presence_times: dict[str, float] = {}
        self.ack_times: dict[str, float] = {}
        self.event_times: dict[str, float] = {}
        self.seen_events: set[str] = set()
        self.duplicate_deliveries = 0
        self.logical_duplicates = 0

    async def handler(self, websocket: ServerConnection) -> None:
        self.connection = websocket
        try:
            first = GatewayEnvelope.model_validate_json(await websocket.recv())
            if first.message_type != GatewayMessageType.GATEWAY_HELLO:
                return
            self.connected.set()
            async for raw in websocket:
                envelope = GatewayEnvelope.model_validate_json(raw)
                now = time.perf_counter()
                if envelope.message_type == GatewayMessageType.AGENT_HELLO:
                    self.agent_hellos[envelope.source_id] = now
                elif envelope.message_type == GatewayMessageType.PRESENCE:
                    self.presence_times[envelope.correlation_id] = now
                elif envelope.message_type == GatewayMessageType.ACK:
                    ack = Ack.model_validate(envelope.payload)
                    self.ack_times[ack.command_id] = now
                elif envelope.message_type == GatewayMessageType.EVENT:
                    event = Event.model_validate(envelope.payload)
                    duplicate = event.event_id in self.seen_events
                    if duplicate:
                        self.duplicate_deliveries += 1
                    self.seen_events.add(event.event_id)
                    self.event_times[event.event_id] = now
                    receipt = EventReceipt(
                        protocol_version=2,
                        event_id=event.event_id,
                        status=(
                            EventReceiptStatus.ALREADY_PROCESSED
                            if duplicate
                            else EventReceiptStatus.ACCEPTED
                        ),
                        received_at=datetime.now(UTC),
                    )
                    receipt_envelope = GatewayEnvelope(
                            protocol_version=2,
                            message_type=GatewayMessageType.EVENT_RECEIPT,
                            message_id=f"receipt_{event.event_id}",
                            correlation_id=envelope.correlation_id,
                            source_id="backend",
                            target_id=self.gateway_id,
                            payload=receipt.model_dump(mode="json"),
                        )
                    try:
                        async with self.send_lock:
                            await websocket.send(receipt_envelope.to_json())
                    except ConnectionClosed:
                        pass
        finally:
            if self.connection is websocket:
                self.connection = None
                self.connected.clear()

    async def send(self, envelope: GatewayEnvelope) -> None:
        if self.connection is None:
            raise OSError("benchmark Backend is disconnected")
        async with self.send_lock:
            await self.connection.send(envelope.to_json())


class SimulatedAgent:
    def __init__(self, agent_id: str, url: str, token: str):
        self.agent_id = agent_id
        self.url = url
        self.token = token
        self.websocket: ClientConnection | None = None
        self.receiver: asyncio.Task[None] | None = None
        self.wrong_target_count = 0
        self.command_received: dict[str, float] = {}

    async def open(self) -> None:
        self.websocket = await connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.token}"},
            open_timeout=10,
            max_queue=1024,
        )
        hello = AgentHello(
            protocol_version=2,
            agent_id=self.agent_id,
            hostname=f"BENCH-{self.agent_id}",
            ip_address="192.0.2.1",
            agent_version="benchmark",
        )
        await self._send(
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.AGENT_HELLO,
                message_id=f"hello_{self.agent_id}",
                correlation_id=f"hello_{self.agent_id}",
                source_id=self.agent_id,
                target_id="gateway",
                payload=hello.model_dump(mode="json"),
            )
        )
        self.receiver = asyncio.create_task(self._receive(), name=f"bench-{self.agent_id}")

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
        if self.receiver is not None:
            await asyncio.gather(self.receiver, return_exceptions=True)
        self.websocket = None
        self.receiver = None

    async def presence(self, sequence: int) -> tuple[str, float]:
        correlation = f"presence_{self.agent_id}_{sequence}"
        value = Presence(
            protocol_version=2,
            agent_id=self.agent_id,
            last_seen=datetime.now(UTC),
            health=PresenceHealth.ONLINE,
            agent_version="benchmark",
        )
        started = time.perf_counter()
        await self._send(
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.PRESENCE,
                message_id=correlation,
                correlation_id=correlation,
                source_id=self.agent_id,
                target_id="backend",
                payload=value.model_dump(mode="json", exclude_none=True),
            )
        )
        return correlation, started

    async def event(self, sequence: int) -> tuple[str, float]:
        event_id = f"event_{self.agent_id}_{sequence}"
        event = Event(
            protocol_version=2,
            event_id=event_id,
            session_id="BENCH-SESSION",
            agent_id=self.agent_id,
            event_type=EventType.POLICY_VIOLATION,
            occurred_at=datetime.now(UTC),
            sequence=sequence,
            payload={
                "severity": "WARNING",
                "category": "BENCHMARK",
                "action": "OBSERVED",
            },
            correlation_id=event_id,
        )
        started = time.perf_counter()
        await self._send(
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.EVENT,
                message_id=event_id,
                correlation_id=event_id,
                source_id=self.agent_id,
                target_id="backend",
                payload=event.model_dump(mode="json", exclude_none=True),
            )
        )
        return event_id, started

    async def _receive(self) -> None:
        assert self.websocket is not None
        async for raw in self.websocket:
            envelope = GatewayEnvelope.model_validate_json(raw)
            if envelope.message_type != GatewayMessageType.COMMAND:
                continue
            command = Command.model_validate(envelope.payload)
            if command.target_id != self.agent_id:
                self.wrong_target_count += 1
                continue
            self.command_received[command.command_id] = time.perf_counter()
            ack = Ack(
                protocol_version=2,
                ack_id=f"ack_{command.command_id}",
                command_id=command.command_id,
                status=AckStatus.SUCCEEDED,
                service_version="benchmark",
                occurred_at=datetime.now(UTC),
                correlation_id=command.correlation_id,
            )
            await self._send(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.ACK,
                    message_id=ack.ack_id,
                    correlation_id=ack.correlation_id,
                    source_id=self.agent_id,
                    target_id="backend",
                    payload=ack.model_dump(mode="json", exclude_none=True),
                )
            )

    async def _send(self, envelope: GatewayEnvelope) -> None:
        if self.websocket is None:
            raise OSError("simulated Agent is disconnected")
        await self.websocket.send(envelope.to_json())


class Benchmark:
    def __init__(
        self,
        agents: int,
        events_per_agent: int,
        timeout: float,
        queue_max_messages: int,
    ):
        self.count = agents
        self.events_per_agent = events_per_agent
        self.timeout = timeout
        self.queue_max_messages = queue_max_messages
        self.gateway_id = "GW-BENCH"
        self.agent_token = "benchmark-agent-credential"
        self.backend = FakeBackend(self.gateway_id)
        self.backend_port = _free_port()
        self.gateway_port = _free_port()
        self.backend_server = None
        self.gateway_server: uvicorn.Server | None = None
        self.gateway_app = None
        self.gateway_task: asyncio.Task[None] | None = None
        self.temporary = tempfile.TemporaryDirectory(prefix="eecp-benchmark-")
        self.event_buffer: EventBuffer | None = None
        self.agents: list[SimulatedAgent] = []

    async def start(self) -> None:
        self.backend_server = await serve(
            self.backend.handler, "127.0.0.1", self.backend_port, compression=None
        )
        settings = GatewaySettings(
            gateway_id=self.gateway_id,
            room_id="BENCH",
            version="benchmark",
            listen_host="127.0.0.1",
            listen_port=self.gateway_port,
            tls_certfile=None,
            tls_keyfile=None,
            backend_ws_url=f"ws://127.0.0.1:{self.backend_port}",
            gateway_bootstrap_token="benchmark-gateway-credential",
            agent_bootstrap_token=self.agent_token,
            allow_plaintext_ws=True,
            reconnect_initial_seconds=0.05,
            reconnect_max_seconds=0.25,
            presence_timeout_seconds=30,
            health_interval_seconds=1,
            presence_offline_timeout_seconds=60,
            event_buffer_path=str(Path(self.temporary.name) / "events.db"),
            event_retry_initial_seconds=1.0,
            event_retry_max_seconds=2.0,
            event_retry_jitter_ratio=0,
            event_flush_interval_seconds=0.05,
            event_flush_batch_size=50,
            environment="development",
            backend_queue_max_messages=self.queue_max_messages,
        )
        self.event_buffer = EventBuffer(
            settings.event_buffer_path,
            max_rows=settings.event_buffer_max_rows,
            degraded_threshold=settings.event_buffer_degraded_threshold,
        )
        self.gateway_app = create_app(settings, event_buffer=self.event_buffer)
        config = uvicorn.Config(
            self.gateway_app,
            host="127.0.0.1",
            port=self.gateway_port,
            log_level="error",
            access_log=False,
        )
        self.gateway_server = uvicorn.Server(config)
        self.gateway_task = asyncio.create_task(self.gateway_server.serve())
        await _wait_until(lambda: self.gateway_server.started, self.timeout, "Gateway startup")
        await asyncio.wait_for(self.backend.connected.wait(), timeout=self.timeout)

    async def stop(self) -> None:
        await asyncio.gather(*(agent.close() for agent in self.agents), return_exceptions=True)
        if self.gateway_server is not None:
            self.gateway_server.should_exit = True
        if self.gateway_task is not None:
            await asyncio.gather(self.gateway_task, return_exceptions=True)
        if self.backend_server is not None:
            self.backend_server.close()
            await self.backend_server.wait_closed()
        self.temporary.cleanup()

    async def connect_agents(self) -> Result:
        started = time.perf_counter()
        self.agents = [
            SimulatedAgent(
                f"AGT-{index:05d}",
                f"ws://127.0.0.1:{self.gateway_port}/ws/agents",
                self.agent_token,
            )
            for index in range(self.count)
        ]
        starts = {agent.agent_id: time.perf_counter() for agent in self.agents}
        values = await asyncio.gather(
            *(agent.open() for agent in self.agents), return_exceptions=True
        )
        failures = sum(isinstance(value, BaseException) for value in values)
        await _wait_until(
            lambda: len(self.backend.agent_hellos) >= self.count - failures,
            self.timeout,
            "Agent hello forwarding",
        )
        latencies = [
            (received - starts[agent_id]) * 1000
            for agent_id, received in self.backend.agent_hellos.items()
            if agent_id in starts
        ]
        elapsed = time.perf_counter() - started
        return Result(
            scenario="connections",
            agents=self.count,
            duration_seconds=round(elapsed, 3),
            successful_connections=self.count - failures,
            failed_connections=failures,
            throughput_per_second=round((self.count - failures) / elapsed, 3),
            **_latencies(latencies),
        )

    async def presence(self) -> Result:
        started = time.perf_counter()
        sent = dict(await asyncio.gather(*(agent.presence(1) for agent in self.agents)))
        await _wait_until(
            lambda: all(key in self.backend.presence_times for key in sent),
            self.timeout,
            "presence forwarding",
        )
        latencies = [
            (self.backend.presence_times[key] - value) * 1000 for key, value in sent.items()
        ]
        elapsed = time.perf_counter() - started
        return Result(
            scenario="presence",
            agents=self.count,
            duration_seconds=round(elapsed, 3),
            messages_sent=len(sent),
            messages_received=len(latencies),
            throughput_per_second=round(len(latencies) / elapsed, 3),
            **_latencies(latencies),
        )

    async def commands(self) -> Result:
        started = time.perf_counter()
        starts: dict[str, float] = {}
        for index, agent in enumerate(self.agents):
            command_id = f"CMD-{index:05d}"
            command = Command(
                protocol_version=2,
                command_id=command_id,
                command_type=CommandType.HEALTH_CHECK,
                target_id=agent.agent_id,
                session_id="BENCH-SESSION",
                issued_at=datetime.now(UTC),
                deadline=datetime.now(UTC) + timedelta(minutes=1),
                payload=HealthCheckPayload(nonce=command_id),
                correlation_id=command_id,
            )
            starts[command_id] = time.perf_counter()
            await self.backend.send(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.COMMAND,
                    message_id=f"message_{command_id}",
                    correlation_id=command_id,
                    source_id=self.gateway_id,
                    target_id=agent.agent_id,
                    payload=command.model_dump(mode="json", exclude_none=True),
                )
            )
        await _wait_until(
            lambda: all(key in self.backend.ack_times for key in starts),
            self.timeout,
            "command ACKs",
        )
        latencies = [
            (self.backend.ack_times[key] - value) * 1000 for key, value in starts.items()
        ]
        elapsed = time.perf_counter() - started
        wrong = sum(agent.wrong_target_count for agent in self.agents)
        return Result(
            scenario="command",
            agents=self.count,
            duration_seconds=round(elapsed, 3),
            commands_sent=len(starts),
            acks_received=len(latencies),
            wrong_target_count=wrong,
            throughput_per_second=round(len(latencies) / elapsed, 3),
            **_latencies(latencies),
        )

    async def events(self, *, sequence_offset: int = 0) -> Result:
        started = time.perf_counter()
        pairs = await asyncio.gather(
            *(
                agent.event(sequence_offset + sequence)
                for agent in self.agents
                for sequence in range(self.events_per_agent)
            )
        )
        sent = dict(pairs)
        await _wait_until(
            lambda: all(key in self.backend.event_times for key in sent),
            self.timeout,
            "event receipts",
        )
        await self._wait_backlog_zero()
        latencies = [
            (self.backend.event_times[key] - value) * 1000 for key, value in sent.items()
        ]
        elapsed = time.perf_counter() - started
        accepted = len([key for key in sent if key in self.backend.seen_events])
        return Result(
            scenario="event",
            agents=self.count,
            duration_seconds=round(elapsed, 3),
            events_sent=len(sent),
            events_accepted=accepted,
            event_loss=len(sent) - accepted,
            logical_duplicates=self.backend.logical_duplicates,
            throughput_per_second=round(accepted / elapsed, 3),
            **_latencies(latencies),
        )

    async def wan(self) -> Result:
        assert self.backend_server is not None
        self.backend_server.close()
        await self.backend_server.wait_closed()
        self.backend_server = None
        await _wait_until(
            lambda: not self.backend.connected.is_set(), self.timeout, "WAN disconnect"
        )
        started = time.perf_counter()
        duplicate_deliveries_before = self.backend.duplicate_deliveries
        pairs = await asyncio.gather(
            *(agent.event(10_000 + index) for index, agent in enumerate(self.agents))
        )
        sent = dict(pairs)
        await _wait_until(
            lambda: not self.backend.connected.is_set(), self.timeout, "offline state"
        )
        assert self.event_buffer is not None
        peak = 0
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            peak = max(peak, self.event_buffer.health().pending_event_count)
            if peak >= len(sent):
                break
            await asyncio.sleep(0.02)
        self.backend_server = await serve(
            self.backend.handler, "127.0.0.1", self.backend_port, compression=None
        )
        await asyncio.wait_for(self.backend.connected.wait(), timeout=self.timeout)
        flush_started = time.perf_counter()
        while time.monotonic() < deadline + self.timeout:
            if self.event_buffer.health().pending_event_count == 0:
                break
            await asyncio.sleep(0.02)
        await _wait_until(
            lambda: all(key in self.backend.event_times for key in sent),
            self.timeout,
            "WAN event flush",
        )
        flush_seconds = time.perf_counter() - flush_started
        elapsed = time.perf_counter() - started
        accepted = len([key for key in sent if key in self.backend.seen_events])
        return Result(
            scenario="wan",
            agents=self.count,
            duration_seconds=round(elapsed, 3),
            events_sent=len(sent),
            events_accepted=accepted,
            event_loss=len(sent) - accepted,
            logical_duplicates=self.backend.logical_duplicates,
            peak_backlog=peak,
            flush_seconds=round(flush_seconds, 3),
            throughput_per_second=round(accepted / elapsed, 3),
            notes=[
                "at-least-once duplicate deliveries deduplicated: "
                f"{self.backend.duplicate_deliveries - duplicate_deliveries_before}"
            ],
        )

    async def reconnect(self) -> Result:
        await asyncio.gather(*(agent.close() for agent in self.agents))
        self.backend.agent_hellos.clear()
        started = time.perf_counter()
        starts = {agent.agent_id: time.perf_counter() for agent in self.agents}
        values = await asyncio.gather(
            *(agent.open() for agent in self.agents), return_exceptions=True
        )
        failures = sum(isinstance(value, BaseException) for value in values)
        await _wait_until(
            lambda: len(self.backend.agent_hellos) >= self.count - failures,
            self.timeout,
            "Agent reconnect forwarding",
        )
        latencies = [
            (received - starts[agent_id]) * 1000
            for agent_id, received in self.backend.agent_hellos.items()
            if agent_id in starts
        ]
        elapsed = time.perf_counter() - started
        return Result(
            scenario="reconnect",
            agents=self.count,
            duration_seconds=round(elapsed, 3),
            successful_connections=self.count - failures,
            failed_connections=failures,
            throughput_per_second=round((self.count - failures) / elapsed, 3),
            **_latencies(latencies),
        )

    async def _wait_backlog_zero(self) -> None:
        deadline = time.monotonic() + self.timeout
        assert self.event_buffer is not None
        while time.monotonic() < deadline:
            if self.event_buffer.health().pending_event_count == 0:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError("timed out waiting for Gateway backlog to drain")


async def _run(args: argparse.Namespace) -> list[Result]:
    benchmark = Benchmark(
        args.agents,
        args.events_per_agent,
        args.timeout,
        args.queue_max_messages,
    )
    results: list[Result] = []
    if args.measure_memory:
        tracemalloc.start()
    try:
        await benchmark.start()
        connection = await benchmark.connect_agents()
        results.append(connection)
        requested = set(args.scenario)
        if "all" in requested or "presence" in requested:
            results.append(await benchmark.presence())
        if "all" in requested or "command" in requested:
            results.append(await benchmark.commands())
        if "all" in requested or "event" in requested:
            results.append(await benchmark.events())
        if "all" in requested or "wan" in requested:
            results.append(await benchmark.wan())
        if "all" in requested or "reconnect" in requested:
            results.append(await benchmark.reconnect())
        if "soak" in requested:
            deadline = time.monotonic() + args.duration
            sequence = 20_000
            while time.monotonic() < deadline:
                results.append(await benchmark.presence())
                results.append(await benchmark.events(sequence_offset=sequence))
                sequence += args.events_per_agent
                await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        peak_mib = None
        if args.measure_memory:
            _current, peak = tracemalloc.get_traced_memory()
            peak_mib = round(peak / 1024 / 1024, 3)
        for result in results:
            result.gateway_python_peak_mib = peak_mib
            if benchmark.gateway_app is not None:
                result.queue_saturation_count = (
                    benchmark.gateway_app.state.uplink.queue_saturation_count
                )
            result.wrong_target_count = max(
                result.wrong_target_count,
                sum(agent.wrong_target_count for agent in benchmark.agents),
            )
    finally:
        if args.measure_memory:
            tracemalloc.stop()
        await benchmark.stop()
    return results


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="EECP Local Gateway benchmark")
    parser.add_argument("--agents", type=int, default=50)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=("all", "presence", "command", "event", "wan", "reconnect", "soak"),
        default=[],
    )
    parser.add_argument("--events-per-agent", type=int, default=1)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--queue-max-messages", type=int, default=4096)
    parser.add_argument("--measure-memory", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if (
        args.agents < 1
        or args.events_per_agent < 1
        or args.duration <= 0
        or args.queue_max_messages < 1
    ):
        parser.error("agents, events-per-agent, duration, and queue limit must be positive")
    if not args.scenario:
        args.scenario = ["all"]
    started = datetime.now(UTC)
    results = asyncio.run(_run(args))
    payload = {
        "format": "eecp-phase9-benchmark/v1",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "environment": {
            "transport": "real loopback WebSocket",
            "gateway": "in-process uvicorn using production Gateway components",
            "backend": "Protocol v2 benchmark peer (no PostgreSQL/Redis in timed path)",
            "tls": False,
        },
        "configuration": {
            "agents": args.agents,
            "scenarios": args.scenario,
            "events_per_agent": args.events_per_agent,
            "gateway_backend_queue_max_messages": args.queue_max_messages,
        },
        "results": [asdict(result) for result in results],
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{encoded}\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
