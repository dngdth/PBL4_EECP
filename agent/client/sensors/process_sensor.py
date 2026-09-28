from __future__ import annotations

import csv
import io
import subprocess
import sys
from collections.abc import Callable, Iterable
from typing import Any

from agent.domain.policy import parse_policy_payload


class ProcessSensor:
    """Detect denied process absent/present transitions without enforcing them."""

    def __init__(
        self,
        report: Callable[[str, str], None],
        *,
        process_provider: Callable[[], Iterable[str]] | None = None,
    ):
        self._report = report
        self._process_provider = process_provider or running_process_names
        self._session_id: str | None = None
        self._denied: set[str] = set()
        self._active: set[str] = set()

    def activate(self, session_id: str, payload: dict[str, Any]) -> None:
        specification = parse_policy_payload(payload)
        denied = set(specification.denied_applications)
        if self._session_id != session_id or self._denied != denied:
            self._active.clear()
        self._session_id = session_id
        self._denied = denied

    def deactivate(self) -> None:
        self._session_id = None
        self._denied.clear()
        self._active.clear()

    def poll(self) -> None:
        if self._session_id is None:
            return
        running = {name.strip().lower() for name in self._process_provider()}
        detected = running & self._denied
        for process_name in sorted(detected - self._active):
            self._report(self._session_id, process_name)
        self._active = detected


def running_process_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError("cannot enumerate running processes")
        return tuple(
            row[0].strip().lower()
            for row in csv.reader(io.StringIO(result.stdout))
            if row
        )
    result = subprocess.run(
        ["ps", "-eo", "comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OSError("cannot enumerate running processes")
    return tuple(line.strip().rsplit("/", 1)[-1].lower() for line in result.stdout.splitlines())
