from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from contracts.v2 import ServiceHealth


class PollingSensor(Protocol):
    def activate(self, session_id: str, payload: dict[str, Any]) -> None: ...
    def deactivate(self) -> None: ...
    def poll(self) -> None: ...


class SensorSuite:
    def __init__(
        self,
        sensors: tuple[PollingSensor, ...],
        *,
        health_sensor: PollingSensor | None = None,
        health_update: Callable[[ServiceHealth, str | None], None] | None = None,
        log: Callable[[str], None] = print,
    ):
        self._sensors = sensors
        self._health_sensor = health_sensor
        self._health_update = health_update
        self._log = log

    def activate(self, session_id: str, payload: dict[str, Any]) -> None:
        for sensor in self._sensors:
            sensor.activate(session_id, payload)

    def deactivate(self) -> None:
        for sensor in self._sensors:
            sensor.deactivate()

    def poll(self) -> None:
        for sensor in self._sensors:
            try:
                sensor.poll()
            except (OSError, ValueError) as exc:
                self._log(f"Sensor {type(sensor).__name__} failed: {exc}")
        if self._health_sensor is not None and self._health_update is not None:
            self._health_update(
                self._health_sensor.service_health,
                self._health_sensor.active_policy_hash,
            )
