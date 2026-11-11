from __future__ import annotations

import hashlib
import io
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    frame_id: str
    captured_at: datetime
    data: bytes
    width: int
    height: int
    monitor_id: str = "virtual-desktop"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


_DPI_AWARE_INITIALIZED = False


def ensure_process_dpi_aware() -> bool:
    """Best-effort activation of Per-Monitor DPI awareness on Windows."""
    global _DPI_AWARE_INITIALIZED
    if _DPI_AWARE_INITIALIZED:
        return True
    if sys.platform != "win32":
        return False

    import ctypes

    # 1. Try Windows 10 v1703+ DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4)
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                _DPI_AWARE_INITIALIZED = True
                return True
    except Exception:
        pass

    # 2. Try Windows 8.1 / 10 early PROCESS_PER_MONITOR_DPI_AWARE (2)
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        if hasattr(shcore, "SetProcessDpiAwareness"):
            shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            shcore.SetProcessDpiAwareness.restype = ctypes.c_long
            if shcore.SetProcessDpiAwareness(2) == 0:
                _DPI_AWARE_INITIALIZED = True
                return True
    except Exception:
        pass

    # 3. Fallback to basic system-wide DPI awareness
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if hasattr(user32, "SetProcessDPIAware"):
            user32.SetProcessDPIAware.restype = ctypes.c_bool
            if user32.SetProcessDPIAware():
                _DPI_AWARE_INITIALIZED = True
                return True
    except Exception:
        pass

    return False


class WindowsScreenCapture:
    """Pillow/GDI capture in the normal user's desktop, including all monitors.

    Frames are encoded before returning. No screenshot is written to the client disk.
    The original bitmap is transient; resizing cannot remove capture-time memory peaks.
    """

    def __init__(self, low_width: int = 960, high_width: int = 2560):
        if not 320 <= low_width <= high_width <= 7680:
            raise ValueError("invalid screen capture widths")
        self.low_width = low_width
        self.high_width = high_width
        ensure_process_dpi_aware()

    def __call__(self, high_quality: bool = False) -> CapturedFrame:
        if sys.platform != "win32":
            raise OSError("desktop capture is supported only on Windows")
        # A locked/secure desktop is unavailable, not a reason to switch desktops.
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        desktop = user32.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            raise OSError("interactive desktop is unavailable or locked")
        try:
            name = ctypes.create_unicode_buffer(256)
            required = wintypes.DWORD()
            if (
                not user32.GetUserObjectInformationW(
                    desktop, 2, name, ctypes.sizeof(name), ctypes.byref(required)
                )
                or name.value.casefold() != "default"
            ):
                raise OSError("secure/locked desktop cannot be captured")
        finally:
            user32.CloseDesktop(desktop)
        try:
            from PIL import Image, ImageGrab
        except ImportError as exc:
            raise OSError("screen capture requires Pillow; run uv sync --all-packages") from exc
        captured_at = datetime.now(UTC)
        with ImageGrab.grab(all_screens=True) as original:
            # Bound compression work for extreme virtual desktops.
            if original.width * original.height > 80_000_000:
                raise OSError("virtual desktop exceeds capture pixel budget")
            with original.convert("RGB") as image:
                width = self.high_width if high_quality else self.low_width
                image.thumbnail((width, width), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=85 if high_quality else 40)
                data = output.getvalue()
                if len(data) > 2 * 1024 * 1024:
                    raise OSError("compressed screenshot exceeds 2 MiB")
                return CapturedFrame(uuid.uuid4().hex, captured_at, data, image.width, image.height)


@dataclass(frozen=True, slots=True)
class ScreenSettings:
    interval_seconds: float = 2.0
    pre_seconds: float = 30.0
    post_seconds: float = 15.0
    retention_seconds: float = 900.0
    ring_max_frames: int = 15
    ring_max_bytes: int = 8 * 1024 * 1024
    evidence_max_bytes: int = 32 * 1024 * 1024
    evidence_max_items: int = 64
    merge_seconds: float = 10.0

    def __post_init__(self):
        if any(
            value <= 0
            for value in (
                self.interval_seconds,
                self.pre_seconds,
                self.post_seconds,
                self.retention_seconds,
                self.ring_max_frames,
                self.ring_max_bytes,
                self.evidence_max_bytes,
                self.evidence_max_items,
                self.merge_seconds,
            )
        ):
            raise ValueError("screen limits must be positive")
        if self.retention_seconds <= self.pre_seconds + self.post_seconds:
            raise ValueError("evidence retention must exceed the capture window")

    @classmethod
    def from_env(cls) -> ScreenSettings:
        return cls(
            interval_seconds=float(os.getenv("EECP_SCREEN_INTERVAL_SECONDS", "2")),
            pre_seconds=float(os.getenv("EECP_SCREEN_PRE_SECONDS", "30")),
            post_seconds=float(os.getenv("EECP_SCREEN_POST_SECONDS", "15")),
            retention_seconds=float(os.getenv("EECP_SCREEN_RETENTION_SECONDS", "900")),
            ring_max_frames=int(os.getenv("EECP_SCREEN_RING_MAX_FRAMES", "15")),
            ring_max_bytes=int(os.getenv("EECP_SCREEN_RING_MAX_BYTES", str(8 * 1024 * 1024))),
            evidence_max_bytes=int(
                os.getenv("EECP_SCREEN_EVIDENCE_MAX_BYTES", str(32 * 1024 * 1024))
            ),
            evidence_max_items=int(os.getenv("EECP_SCREEN_EVIDENCE_MAX_ITEMS", "64")),
        )
