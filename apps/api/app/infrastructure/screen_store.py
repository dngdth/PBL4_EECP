from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contracts.v2.screen import AUTOMATIC_FRAME_LIMITS, ScreenFrame, ScreenRequest, ScreenResponse


class ScreenStore:
    """Business-DB metadata plus content-addressed files on a persistent volume.

    Bytes are fsynced before the DB commit and its receipt. The same image shared by
    multiple evidence sets uses one file. Cleanup respects remaining DB references.
    """

    def __init__(
        self,
        database,
        root: Path,
        retention_days: int = 14,
        max_bytes: int = 10 * 1024 * 1024 * 1024,
    ):
        if min(retention_days, max_bytes) <= 0:
            raise ValueError("screen retention and storage limits must be positive")
        self.database = database
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self.lock = threading.RLock()
        self.max_bytes = max_bytes
        self._file_sizes = {p.name: p.stat().st_size for p in self.root.glob("*.bin")}

    @contextmanager
    def connection(self):
        connection = self.database.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def frame_key(evidence_id: str, frame_id: str) -> str:
        return hashlib.sha256(f"{evidence_id}:{frame_id}".encode()).hexdigest()

    def save_request(self, value: ScreenRequest) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO screen_requests
                (id,session_id,agent_id,payload,status,created_at,deadline)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    value.request_id,
                    value.session_id,
                    value.agent_id,
                    value.to_json(),
                    "capturing",
                    value.issued_at.isoformat(),
                    value.deadline.isoformat(),
                ),
            )

    def get_request(self, request_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM screen_requests WHERE id=?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["payload"] = json.loads(value["payload"])
            response = json.loads(value["response"]) if value["response"] else None
            value["response"] = response
            if response and response.get("evidence_id"):
                value["frames"] = self._frames_with_conn(connection, response["evidence_id"])
                expected = {frame["frame_id"] for frame in response.get("frames", [])}
                stored = {frame["frame_id"] for frame in value["frames"]}
                if expected and expected <= stored:
                    value["status"] = "available"
            if value["status"] in {"capturing", "uploading"} and (
                datetime.fromisoformat(value["deadline"]) < datetime.now(UTC)
            ):
                value["status"] = "unavailable"
            return value

    def save_response(self, value: ScreenResponse) -> None:
        with self.lock, self.connection() as connection:
            if value.request_id.startswith("notice_"):
                if value.evidence_id is None or value.request_id != f"notice_{value.evidence_id}":
                    raise ValueError("invalid evidence notice")
                self._upsert_evidence(connection, value)
                return
            row = connection.execute(
                "SELECT * FROM screen_requests WHERE id=?", (value.request_id,)
            ).fetchone()
            if (
                row is None
                or row["session_id"] != value.session_id
                or (row["agent_id"] != value.agent_id)
            ):
                raise ValueError("screenshot response does not match authorized request")
            request = ScreenRequest.model_validate_json(row["payload"])
            if row["response"]:
                previous = ScreenResponse.model_validate_json(row["response"])
                if previous.frames and (
                    previous.evidence_id != value.evidence_id or previous.frames != value.frames
                ):
                    raise ValueError("authorized screenshot manifest cannot be replaced")
            if request.evidence_id is not None and request.evidence_id != value.evidence_id:
                raise ValueError("screenshot response evidence does not match request")
            if (
                len(value.frames) > request.max_frames
                or sum(frame.size_bytes for frame in value.frames) > request.max_bytes
            ):
                raise ValueError("screenshot response exceeds authorized limits")
            if value.evidence_id and (
                value.available_until is not None
                or not connection.execute(
                    "SELECT id FROM screen_evidence WHERE id=?", (value.evidence_id,)
                ).fetchone()
            ):
                self._upsert_evidence(connection, value)
            connection.execute(
                "UPDATE screen_requests SET response=?,status=? WHERE id=?",
                (value.to_json(), value.status.value, value.request_id),
            )

    def _upsert_evidence(self, connection, value) -> None:
        old = connection.execute(
            "SELECT * FROM screen_evidence WHERE id=?", (value.evidence_id,)
        ).fetchone()
        if old is not None and (
            old["session_id"] != value.session_id
            or (old["agent_id"] != value.agent_id)
            or old["event_id"] != value.event_id
        ):
            raise ValueError("evidence identity conflict")
        connection.execute(
            """INSERT INTO screen_evidence
            (id,session_id,agent_id,event_id,status,available_until,reason,created_at)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            status=excluded.status, available_until=excluded.available_until,
            reason=excluded.reason""",
            (
                value.evidence_id,
                value.session_id,
                value.agent_id,
                value.event_id,
                value.status.value,
                value.available_until.isoformat() if value.available_until else None,
                value.reason,
                datetime.now(UTC).isoformat(),
            ),
        )

    def store_frame(self, frame: ScreenFrame, data: bytes) -> dict:
        if len(data) != frame.size_bytes or hashlib.sha256(data).hexdigest() != frame.sha256:
            raise ValueError("screenshot length/checksum mismatch")
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as image:
                expected_format = "JPEG" if frame.content_type == "image/jpeg" else "WEBP"
                if image.format != expected_format or image.size != (frame.width, frame.height):
                    raise ValueError("screenshot format/dimensions do not match metadata")
                image.verify()
        except (OSError, Image.DecompressionBombError, SyntaxError) as exc:
            raise ValueError("invalid encoded screenshot") from exc
        with self.lock, self.connection() as connection:
            # Enforce the automatic-image limit inside the storage lock as well as
            # at the HTTP boundary: concurrent ingests can pass the route check.
            if frame.request_id is None and frame.kind in AUTOMATIC_FRAME_LIMITS:
                same_kind_count = 0
                for row in connection.execute(
                    "SELECT metadata FROM screen_frames WHERE evidence_id=?", (frame.evidence_id,)
                ).fetchall():
                    existing = ScreenFrame.model_validate_json(row["metadata"])
                    if existing.kind == frame.kind and existing.frame_id != frame.frame_id:
                        same_kind_count += 1
                if same_kind_count >= AUTOMATIC_FRAME_LIMITS[frame.kind]:
                    raise ValueError("representative screenshot limit reached")
            key = self.frame_key(frame.evidence_id, frame.frame_id)
            old = connection.execute("SELECT * FROM screen_frames WHERE id=?", (key,)).fetchone()
            if old is not None:
                old_metadata = ScreenFrame.model_validate_json(old["metadata"])
                if old_metadata.model_dump(exclude={"request_id"}) != frame.model_dump(
                    exclude={"request_id"}
                ):
                    raise ValueError("screenshot ID reused with different metadata")
            else:
                notice = ScreenResponse(
                    protocol_version=2,
                    request_id=f"notice_{frame.evidence_id}",
                    agent_id=frame.agent_id,
                    session_id=frame.session_id,
                    evidence_id=frame.evidence_id,
                    event_id=frame.event_id,
                    status="ready",
                    available_until=frame.available_until,
                )
                self._upsert_evidence(connection, notice)
            storage_key = f"{frame.sha256}.bin"
            path = self.root / storage_key
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != frame.sha256:
                if (
                    sum(self._file_sizes.values())
                    - self._file_sizes.get(storage_key, 0)
                    + len(data)
                    > self.max_bytes
                ):
                    raise OSError("Backend screenshot storage quota exceeded")
                temporary = self.root / f"{uuid.uuid4().hex}.tmp"
                try:
                    with temporary.open("wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(path)
                    self._file_sizes[storage_key] = len(data)
                finally:
                    temporary.unlink(missing_ok=True)
            now = datetime.now(UTC)
            connection.execute(
                """INSERT INTO screen_frames
                (id,evidence_id,frame_id,metadata,storage_key,stored_at,expires_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (
                    key,
                    frame.evidence_id,
                    frame.frame_id,
                    frame.to_json(),
                    storage_key,
                    now.isoformat(),
                    (now + timedelta(days=self.retention_days)).isoformat(),
                ),
            )
        return {"server_stored": True, "sha256": frame.sha256, "frame_id": frame.frame_id}

    def evidence(self, evidence_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM screen_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
            if row is None:
                return None
            return self._evidence_value(row, frames=self._frames_with_conn(connection, evidence_id))

    def _evidence_value(self, row, *, frames: list[dict] | None = None) -> dict:
        value = dict(row)
        value["frames"] = frames if frames is not None else self.frames(value["id"])
        value["replay_available"] = (
            bool(value["available_until"])
            and (datetime.fromisoformat(value["available_until"]) > datetime.now(UTC))
            and value["status"] not in {"expired", "unavailable"}
        )
        if value["frames"]:
            value["status"] = "available"
        elif not value["replay_available"]:
            value["status"] = "expired" if value["status"] != "unavailable" else "unavailable"
        return value

    def list_evidence(self, session_id: str, agent_id: str | None = None) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM screen_evidence WHERE session_id=?"
                + (" AND agent_id=?" if agent_id else "")
                + " ORDER BY created_at DESC LIMIT 200",
                (session_id, agent_id) if agent_id else (session_id,),
            ).fetchall()
            evidence_ids = [row["id"] for row in rows]
            frames_map = self._frames_by_evidence_with_conn(connection, evidence_ids)
            return [
                self._evidence_value(row, frames=frames_map.get(row["id"], []))
                for row in rows
            ]

    def linked_telemetry_events(
        self, session_id: str, agent_id: str | None = None
    ) -> list[tuple[str, str, str]]:
        """Return (event_id, workstation_id, evidence_id) for events carrying evidence_id."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id, workstation_id, payload FROM telemetry_events WHERE session_id=?"
                + (" AND workstation_id=?" if agent_id else "")
                + " AND payload LIKE ? ORDER BY occurred_at, id",
                (
                    (session_id, agent_id, '%"evidence_id"%')
                    if agent_id
                    else (session_id, '%"evidence_id"%')
                ),
            ).fetchall()
        linked: list[tuple[str, str, str]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except ValueError:
                continue
            evidence_id = payload.get("evidence_id") if isinstance(payload, dict) else None
            if isinstance(evidence_id, str) and evidence_id:
                linked.append((row["id"], row["workstation_id"], evidence_id))
        return linked

    @staticmethod
    def _format_frame_row(row) -> dict:
        value = json.loads(row["metadata"])
        value["stored_at"] = row["stored_at"]
        value["expires_at"] = row["expires_at"]
        value["url"] = f"/api/v1/screen/evidence/{row['evidence_id']}/frames/{row['frame_id']}"
        return value

    def _frames_with_conn(self, connection, evidence_id: str) -> list[dict]:
        rows = connection.execute(
            "SELECT * FROM screen_frames WHERE evidence_id=? AND expires_at>? "
            "ORDER BY stored_at",
            (evidence_id, datetime.now(UTC).isoformat()),
        ).fetchall()
        values = [self._format_frame_row(row) for row in rows]
        return sorted(values, key=lambda value: value["captured_at"])

    def _frames_by_evidence_with_conn(
        self, connection, evidence_ids: list[str]
    ) -> dict[str, list[dict]]:
        if not evidence_ids:
            return {}
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = connection.execute(
            f"SELECT * FROM screen_frames WHERE evidence_id IN ({placeholders}) "
            "AND expires_at>? ORDER BY stored_at",
            (*evidence_ids, datetime.now(UTC).isoformat()),
        ).fetchall()
        grouped: dict[str, list[dict]] = {eid: [] for eid in evidence_ids}
        for row in rows:
            grouped.setdefault(row["evidence_id"], []).append(self._format_frame_row(row))
        for items in grouped.values():
            items.sort(key=lambda value: value["captured_at"])
        return grouped

    def frames(self, evidence_id: str) -> list[dict]:
        with self.connection() as connection:
            return self._frames_with_conn(connection, evidence_id)

    def read_frame(self, evidence_id: str, frame_id: str) -> tuple[ScreenFrame, bytes] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM screen_frames WHERE id=? AND expires_at>?",
                (self.frame_key(evidence_id, frame_id), datetime.now(UTC).isoformat()),
            ).fetchone()
            if row is None:
                return None
            frame = ScreenFrame.model_validate_json(row["metadata"])
            storage_key = row["storage_key"]
        try:
            data = (self.root / storage_key).read_bytes()
        except FileNotFoundError:
            return None
        if hashlib.sha256(data).hexdigest() != frame.sha256:
            raise OSError("stored screenshot integrity failure")
        return frame, data

    def expire(self) -> None:
        with self.lock, self.connection() as connection:
            now = datetime.now(UTC).isoformat()
            rows = connection.execute(
                "SELECT storage_key FROM screen_frames WHERE expires_at<=?", (now,)
            ).fetchall()
            connection.execute("DELETE FROM screen_frames WHERE expires_at<=?", (now,))
            for row in rows:
                reference = connection.execute(
                    "SELECT id FROM screen_frames WHERE storage_key=? LIMIT 1",
                    (row["storage_key"],),
                ).fetchone()
                if reference is None:
                    (self.root / row["storage_key"]).unlink(missing_ok=True)
                    self._file_sizes.pop(row["storage_key"], None)
            # Files left before a failed DB commit are never returned as evidence.
            for path in self.root.glob("*.bin"):
                if len(path.stem) != 64 or any(c not in "0123456789abcdef" for c in path.stem):
                    continue
                if (
                    datetime.fromtimestamp(path.stat().st_mtime, UTC)
                    < (datetime.now(UTC) - timedelta(days=1))
                    and connection.execute(
                        "SELECT id FROM screen_frames WHERE storage_key=? LIMIT 1", (path.name,)
                    ).fetchone()
                    is None
                ):
                    path.unlink(missing_ok=True)
                    self._file_sizes.pop(path.name, None)
