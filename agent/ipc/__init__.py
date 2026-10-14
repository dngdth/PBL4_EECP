"""Local IPC primitives for the EECP privileged service boundary."""

from agent.ipc.framing import FrameError, FrameTooLarge, decode_frame, encode_frame

__all__ = ["FrameError", "FrameTooLarge", "decode_frame", "encode_frame"]
