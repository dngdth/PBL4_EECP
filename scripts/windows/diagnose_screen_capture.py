"""Measure user-desktop capture in RAM only; never writes or uploads screenshots."""

from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from ctypes import wintypes

from agent.client.screen.buffer import RingBuffer
from agent.client.screen.capture import WindowsScreenCapture


def working_set() -> dict:
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            *(
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError("cannot measure process working set")
    return {
        "working_set_bytes": counters.WorkingSetSize,
        "process_peak_working_set_bytes": counters.PeakWorkingSetSize,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=15)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--low-width", type=int, default=960)
    parser.add_argument("--high-width", type=int, default=2560)
    args = parser.parse_args()
    if sys.platform != "win32" or not 1 <= args.frames <= 100 or args.interval < 0:
        parser.error("requires Windows, 1..100 frames and a nonnegative interval")
    capture = WindowsScreenCapture(args.low_width, args.high_width)
    ring = RingBuffer(30, 15, 8 * 1024 * 1024)
    times, sizes = [], []
    cpu_before = time.process_time()
    wall_before = time.perf_counter()
    initial = working_set()
    for index in range(args.frames):
        before = time.perf_counter()
        value = capture(False)
        times.append(time.perf_counter() - before)
        sizes.append(len(value.data))
        ring.add(value)
        if index < args.frames - 1:
            time.sleep(max(0, args.interval - times[-1]))
    before = time.perf_counter()
    high = capture(True)
    high_seconds = time.perf_counter() - before
    elapsed = time.perf_counter() - wall_before
    cpu = time.process_time() - cpu_before
    print(
        json.dumps(
            {
                "frames_captured": len(times),
                "interval_seconds": args.interval,
                "low_max_width": args.low_width,
                "low_mean_bytes": statistics.mean(sizes),
                "ring_retained_bytes": ring.size_bytes,
                "low_mean_capture_seconds": statistics.mean(times),
                "low_max_capture_seconds": max(times),
                "high_capture_seconds": high_seconds,
                "high_bytes": len(high.data),
                "high_dimensions": [high.width, high.height],
                "wall_seconds": elapsed,
                "cpu_seconds": cpu,
                "cpu_percent_of_one_core": 100 * cpu / elapsed,
                "memory_before": initial,
                "memory_after": working_set(),
                "screenshots_saved": 0,
                "screenshots_uploaded": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
