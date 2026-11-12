from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from agent.client.screen.capture import CapturedFrame


class RingBuffer:
    """Compressed immutable frames; callers serialize access using the manager lock."""

    def __init__(self, seconds: float, max_frames: int, max_bytes: int):
        if min(seconds, max_frames, max_bytes) <= 0:
            raise ValueError("ring limits must be positive")
        self.seconds = seconds
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._frames: deque[CapturedFrame] = deque()
        self.size_bytes = 0

    def add(self, frame: CapturedFrame) -> None:
        self.prune(frame.captured_at)
        if len(frame.data) > self.max_bytes:
            return
        self._frames.append(frame)
        self.size_bytes += len(frame.data)
        while len(self._frames) > self.max_frames or self.size_bytes > self.max_bytes:
            self.size_bytes -= len(self._frames.popleft().data)

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.seconds)
        while self._frames and self._frames[0].captured_at < cutoff:
            self.size_bytes -= len(self._frames.popleft().data)

    def between(self, start: datetime, end: datetime) -> tuple[CapturedFrame, ...]:
        return tuple(frame for frame in self._frames if start <= frame.captured_at <= end)

    def latest(self) -> CapturedFrame | None:
        return self._frames[-1] if self._frames else None

    def clear(self) -> None:
        self._frames.clear()
        self.size_bytes = 0
