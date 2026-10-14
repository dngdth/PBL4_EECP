from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from functools import lru_cache

from agent.ipc.security import PIPE_ACL_SDDL, validate_pipe_acl_sddl

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
PIPE_UNLIMITED_INSTANCES = 255
ERROR_PIPE_CONNECTED = 535
ERROR_OPERATION_ABORTED = 995
SDDL_REVISION_1 = 1


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


def require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows Named Pipe transport is supported only on Windows")


@lru_cache(maxsize=1)
def kernel32():
    require_windows()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    api.WaitNamedPipeW.restype = wintypes.BOOL
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
    ]
    api.CreateNamedPipeW.restype = wintypes.HANDLE
    api.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    api.ConnectNamedPipe.restype = wintypes.BOOL
    api.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    api.DisconnectNamedPipe.restype = wintypes.BOOL
    api.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    api.FlushFileBuffers.restype = wintypes.BOOL
    api.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    api.ReadFile.restype = wintypes.BOOL
    api.WriteFile.argtypes = api.ReadFile.argtypes
    api.WriteFile.restype = wintypes.BOOL
    api.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    api.CancelIoEx.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.LocalFree.argtypes = [wintypes.HLOCAL]
    api.LocalFree.restype = wintypes.HLOCAL
    return api


def create_security_attributes(
    sddl: str = PIPE_ACL_SDDL,
) -> tuple[SECURITY_ATTRIBUTES, int]:
    require_windows()
    validate_pipe_acl_sddl(sddl)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    converter = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    converter.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.ULONG),
    ]
    converter.restype = wintypes.BOOL
    descriptor = wintypes.LPVOID()
    if not converter(sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False
    )
    return attributes, int(descriptor.value)


def free_security_descriptor(descriptor: int) -> None:
    if descriptor:
        kernel32().LocalFree(descriptor)


def close_handle(handle: int | None) -> None:
    if handle is None or handle == INVALID_HANDLE_VALUE:
        return
    api = kernel32()
    api.CancelIoEx(wintypes.HANDLE(handle), None)
    api.CloseHandle(wintypes.HANDLE(handle))


def read_handle(handle: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = wintypes.DWORD()
    ok = kernel32().ReadFile(
        wintypes.HANDLE(handle), buffer, size, ctypes.byref(read), None
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.raw[: read.value]


def write_handle(handle: int, data: bytes) -> int:
    written = wintypes.DWORD()
    buffer = ctypes.create_string_buffer(data)
    ok = kernel32().WriteFile(
        wintypes.HANDLE(handle), buffer, len(data), ctypes.byref(written), None
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return written.value
