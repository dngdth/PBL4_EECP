"""Bounded durable screenshot spool and independent HTTPS forwarding data plane."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from contracts.v2.screen import MAX_FRAME_BYTES, ScreenFrame

router = APIRouter()


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class ScreenSpool:
    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 512 * 1024 * 1024,
        per_agent_bytes: int = 32 * 1024 * 1024,
        ttl_seconds: int = 86400,
        max_items: int = 10000,
    ):
        if min(max_bytes, per_agent_bytes, ttl_seconds, max_items) <= 0:
            raise ValueError("invalid Gateway screenshot spool limits")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.per_agent_bytes = per_agent_bytes
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.lock = threading.RLock()
        self._entries = dict(self._load_records())
        self._next_orphan_cleanup = 0.0

    @staticmethod
    def key(frame: ScreenFrame) -> str:
        return hashlib.sha256(
            f"{frame.evidence_id}:{frame.frame_id}:{frame.request_id or 'auto'}".encode()
        ).hexdigest()

    def _load_records(self) -> list[tuple[str, dict]]:
        values = []
        for path in self.root.glob("*.json"):
            try:
                value = json.loads(path.read_text("utf-8"))
                frame = ScreenFrame.model_validate(value["frame"])
                if path.stem != self.key(frame):
                    continue
                if value.get("state") not in {"pending", "stored", "expired"} or not isinstance(
                    value.get("expires_at"), (float, int)
                ):
                    continue
                values.append((path.stem, value))
            except (OSError, ValueError, KeyError, TypeError):
                continue  # An incomplete record never generates a successful receipt.
        return values

    def records(self) -> list[tuple[str, dict]]:
        with self.lock:
            return [(key, value.copy()) for key, value in self._entries.items()]

    def put(self, frame: ScreenFrame, data: bytes) -> dict:
        if len(data) != frame.size_bytes or hashlib.sha256(data).hexdigest() != frame.sha256:
            raise ValueError("screenshot length/checksum mismatch")
        with self.lock:
            self.cleanup()
            key = self.key(frame)
            manifest = self.root / f"{key}.json"
            if key in self._entries:
                old = self._entries[key]
                if old["frame"] != frame.model_dump(mode="json"):
                    raise ValueError("screenshot identity was reused with different metadata")
                if old["state"] == "pending":
                    image_path = self.root / f"{key}.bin"
                    if (
                        not image_path.exists()
                        or hashlib.sha256(image_path.read_bytes()).hexdigest() != frame.sha256
                    ):
                        atomic_write(image_path, data)
                return self.receipt(old)
            records = self.records()
            pending = [value for _key, value in records if value["state"] == "pending"]
            total = sum(value["frame"]["size_bytes"] for value in pending)
            agent_total = sum(
                value["frame"]["size_bytes"]
                for value in pending
                if value["frame"]["agent_id"] == frame.agent_id
            )
            if (
                len(records) >= self.max_items
                or total + len(data) > self.max_bytes
                or (agent_total + len(data) > self.per_agent_bytes)
            ):
                raise OSError("Gateway screenshot spool quota exceeded")
            now = time.time()
            value = {
                "frame": frame.model_dump(mode="json"),
                "state": "pending",
                "expires_at": now + self.ttl_seconds,
                "attempts": 0,
                "next_attempt": 0,
            }
            atomic_write(self.root / f"{key}.bin", data)
            atomic_write(manifest, json.dumps(value, separators=(",", ":")).encode())
            self._entries[key] = value
            return self.receipt(value)

    @staticmethod
    def receipt(value: dict) -> dict:
        return {
            "sha256": value["frame"]["sha256"],
            "gateway_received": value["state"] in {"pending", "stored"},
            "server_stored": value["state"] == "stored",
            "state": value["state"],
        }

    def complete(self, key: str, receipt: dict) -> None:
        with self.lock:
            manifest = self.root / f"{key}.json"
            value = self._entries[key].copy()
            if receipt.get("sha256") != value["frame"]["sha256"] or (
                receipt.get("server_stored") is not True
            ):
                raise ValueError("Backend did not acknowledge durable screenshot storage")
            value["state"] = "stored"
            atomic_write(manifest, json.dumps(value).encode())
            self._entries[key] = value
            (self.root / f"{key}.bin").unlink(missing_ok=True)

    def retry(self, key: str, error: str) -> None:
        with self.lock:
            manifest = self.root / f"{key}.json"
            if key not in self._entries:
                return
            value = self._entries[key].copy()
            value["attempts"] += 1
            value["next_attempt"] = time.time() + min(60, 2 ** min(value["attempts"], 6))
            value["last_error"] = error[:500]
            atomic_write(manifest, json.dumps(value).encode())
            self._entries[key] = value

    def cleanup(self) -> None:
        with self.lock:
            now = time.time()
            for key, value in self.records():
                manifest = self.root / f"{key}.json"
                if now >= value["expires_at"]:
                    if value["state"] == "pending":
                        value["state"] = "expired"
                        value["last_error"] = "Gateway retention expired before server ACK"
                        atomic_write(manifest, json.dumps(value).encode())
                        self._entries[key] = value
                        print(
                            "event=SCREEN_SPOOL_EXPIRED "
                            f"evidence_id={value['frame']['evidence_id']}"
                        )
                    (self.root / f"{key}.bin").unlink(missing_ok=True)
                if now >= value["expires_at"] + self.ttl_seconds:
                    manifest.unlink(missing_ok=True)
                    del self._entries[key]
            # Crash leftovers have no receipt and can be removed after the spool TTL.
            if now < self._next_orphan_cleanup:
                return
            self._next_orphan_cleanup = now + 3600
            for path in self.root.glob("*.bin"):
                if len(path.stem) != 64 or any(c not in "0123456789abcdef" for c in path.stem):
                    continue
                if not path.with_suffix(".json").exists() and (
                    now - path.stat().st_mtime > self.ttl_seconds
                ):
                    path.unlink(missing_ok=True)

    def health(self) -> dict:
        with self.lock:
            records = self.records()
            return {
                "pending_frames": sum(v["state"] == "pending" for _, v in records),
                "pending_bytes": sum(
                    v["frame"]["size_bytes"] for _, v in records if v["state"] == "pending"
                ),
                "expired_frames": sum(v["state"] == "expired" for _, v in records),
            }


def authenticate_agent(settings, agent_id: str, supplied: str) -> None:
    secret = supplied[7:] if supplied.startswith("Bearer ") else ""
    if not secret:
        raise HTTPException(401, "Agent authentication required")
    if settings.agent_credentials_json:
        record = json.loads(settings.agent_credentials_json).get(agent_id, {})
        valid = not record.get("revoked") and secrets.compare_digest(
            hashlib.sha256(secret.encode()).hexdigest(), str(record.get("secret_sha256", ""))
        )
    else:
        valid = bool(settings.agent_bootstrap_token) and secrets.compare_digest(
            secret, settings.agent_bootstrap_token
        )
    if not valid:
        raise HTTPException(403, "Agent identity/credential mismatch")


@router.post("/screen/frames", status_code=202)
async def receive_frame(request: Request) -> dict:
    header = request.headers.get("x-eecp-frame", "")
    if len(header) > 8192:
        raise HTTPException(413, "screenshot metadata too large")
    try:
        frame = ScreenFrame.model_validate_json(header)
    except ValidationError as exc:
        raise HTTPException(422, "invalid screenshot metadata") from exc
    authenticate_agent(
        request.app.state.settings, frame.agent_id, request.headers.get("authorization", "")
    )
    semaphore = request.app.state.screen_upload_slots
    if semaphore.locked():
        raise HTTPException(429, "Gateway screenshot upload concurrency limit")
    async with semaphore:
        data = bytearray()
        try:
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > min(MAX_FRAME_BYTES, frame.size_bytes):
                        raise HTTPException(413, "screenshot byte limit exceeded")
                    data.extend(chunk)
        except TimeoutError as exc:
            raise HTTPException(408, "screenshot upload timed out") from exc
        try:
            return await asyncio.to_thread(request.app.state.screen_spool.put, frame, bytes(data))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(507, "Gateway screenshot storage unavailable or full") from exc


class ScreenForwarder:
    def __init__(self, settings, spool: ScreenSpool):
        self.settings = settings
        self.spool = spool
        parts = urlsplit(settings.backend_ws_url)
        self.url = urlunsplit(
            (
                "https" if parts.scheme == "wss" else "http",
                parts.netloc,
                f"/api/v2/screen/ingest/{settings.gateway_id}",
                "",
                "",
            )
        )
        self.context = ssl.create_default_context(cafile=settings.backend_ca_file)

    def forward(self, key: str, value: dict) -> None:
        try:
            frame = ScreenFrame.model_validate(value["frame"])
            data = (self.spool.root / f"{key}.bin").read_bytes()
            request = urllib.request.Request(
                self.url,
                data=data,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.settings.gateway_bootstrap_token}",
                    "Content-Type": frame.content_type,
                    "X-EECP-Frame": frame.to_json(),
                },
            )
            with urllib.request.urlopen(request, context=self.context, timeout=10) as response:
                receipt = json.loads(response.read(8192))
            self.spool.complete(key, receipt)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.spool.retry(key, str(exc))

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.spool.cleanup)
                values = await asyncio.to_thread(self.spool.records)
                pending = [
                    (k, v)
                    for k, v in values
                    if v["state"] == "pending" and v["next_attempt"] <= time.time()
                ]
                await asyncio.gather(
                    *(asyncio.to_thread(self.forward, k, v) for k, v in pending[:4])
                )
            except (OSError, ValueError, KeyError) as exc:
                print(f"event=SCREEN_FORWARDER_ERROR error_type={type(exc).__name__}")
            await asyncio.sleep(1)
