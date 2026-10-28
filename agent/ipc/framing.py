from __future__ import annotations

import struct
from collections.abc import Callable

HEADER_SIZE = 4


class FrameError(ValueError):
    """The IPC stream does not contain one valid length-prefixed frame."""


class FrameTooLarge(FrameError):
    """The declared or encoded frame exceeds the configured maximum."""


def encode_frame(payload: bytes, max_message_bytes: int) -> bytes:
    size = len(payload)
    _validate_size(size, max_message_bytes)
    return struct.pack("!I", size) + payload


def decode_frame(frame: bytes, max_message_bytes: int) -> bytes:
    if len(frame) < HEADER_SIZE:
        raise FrameError("truncated frame header")
    (size,) = struct.unpack("!I", frame[:HEADER_SIZE])
    _validate_size(size, max_message_bytes)
    payload = frame[HEADER_SIZE:]
    if len(payload) != size:
        raise FrameError("truncated frame payload" if len(payload) < size else "trailing data")
    return payload


def read_frame(read: Callable[[int], bytes], max_message_bytes: int) -> bytes:
    header = read_exact(read, HEADER_SIZE)
    (size,) = struct.unpack("!I", header)
    _validate_size(size, max_message_bytes)
    return read_exact(read, size)


def write_frame(
    write: Callable[[bytes], int], payload: bytes, max_message_bytes: int
) -> None:
    data = encode_frame(payload, max_message_bytes)
    offset = 0
    while offset < len(data):
        written = write(data[offset:])
        if written <= 0:
            raise FrameError("IPC stream disconnected during write")
        offset += written


def read_exact(read: Callable[[int], bytes], size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = read(remaining)
        if not chunk:
            raise FrameError("IPC stream disconnected during read")
        if len(chunk) > remaining:
            raise FrameError("IPC reader returned more bytes than requested")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _validate_size(size: int, max_message_bytes: int) -> None:
    if max_message_bytes < 1:
        raise ValueError("max_message_bytes must be positive")
    if size < 1:
        raise FrameError("frame payload must not be empty")
    if size > max_message_bytes:
        raise FrameTooLarge(
            f"frame payload is {size} bytes; maximum is {max_message_bytes}"
        )
