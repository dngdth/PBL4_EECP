from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from contracts.v2 import ServiceResult


class ReplayJournal:
    """Bounded, atomic local journal for completed privileged commands."""

    def __init__(
        self,
        path: Path,
        capacity: int,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if capacity < 1:
            raise ValueError("replay journal capacity must be positive")
        self._path = path
        self._capacity = capacity
        self._clock = clock
        self._entries = self._load()

    def get(self, command_id: str) -> tuple[str, ServiceResult] | None:
        value = self._entries.get(command_id)
        if value is None:
            return None
        fingerprint, _expires_at, result = value
        self._entries.move_to_end(command_id)
        return fingerprint, result

    def put(
        self,
        command_id: str,
        fingerprint: str,
        expires_at: datetime,
        result: ServiceResult,
    ) -> None:
        self._entries[command_id] = (fingerprint, expires_at, result)
        self._entries.move_to_end(command_id)
        self._prune()
        self._persist()

    def _load(
        self,
    ) -> OrderedDict[str, tuple[str, datetime, ServiceResult]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return OrderedDict()
        except (OSError, json.JSONDecodeError) as exc:
            raise OSError(f"cannot read EECP command replay journal: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise OSError("EECP command replay journal has an unsupported format")
        values = raw.get("entries")
        if not isinstance(values, list):
            raise OSError("EECP command replay journal entries are invalid")
        entries: OrderedDict[str, tuple[str, datetime, ServiceResult]] = OrderedDict()
        try:
            for item in values:
                command_id = str(item["command_id"])
                fingerprint = str(item["fingerprint"])
                expires_at = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00"))
                if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                    raise ValueError("expiry must be timezone-aware")
                result = ServiceResult.model_validate(item["result"])
                entries[command_id] = (fingerprint, expires_at.astimezone(UTC), result)
        except (KeyError, TypeError, ValueError) as exc:
            raise OSError("EECP command replay journal entries are invalid") from exc
        self._entries = entries
        self._prune()
        return entries

    def _prune(self) -> None:
        now = self._clock()
        expired = [
            command_id
            for command_id, (_fingerprint, deadline, _result) in self._entries.items()
            if deadline < now
        ]
        for command_id in expired:
            self._entries.pop(command_id, None)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def _persist(self) -> None:
        payload = {
            "version": 1,
            "entries": [
                {
                    "command_id": command_id,
                    "fingerprint": fingerprint,
                    "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                    "result": result.model_dump(mode="json", exclude_none=True),
                }
                for command_id, (fingerprint, expires_at, result) in self._entries.items()
            ],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self._path)
        except OSError as exc:
            raise OSError(f"cannot persist EECP command replay journal: {exc}") from exc
