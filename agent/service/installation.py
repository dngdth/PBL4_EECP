from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path


def prepare_service_executable() -> Path:
    """Stage the native Service host and its runtime DLLs in this Python environment."""
    import pywintypes
    import win32api
    import win32service

    # CPython 3.12 discovers the venv correctly when the host is beside python.exe.
    host_directory = Path(sys.executable).parent
    python_dll = Path(win32api.GetModuleFileName(sys.dllhandle))
    executable_name = (
        "pythonservice_d.exe" if python_dll.stem.endswith("_d") else "pythonservice.exe"
    )
    installed_executable = host_directory / executable_name
    packaged_executable = Path(win32service.__file__).with_name(executable_name)
    # Older pywin32 installers move the packaged executable into the environment.
    executable_candidates = (
        packaged_executable,
        installed_executable,
        Path(sys.exec_prefix) / executable_name,
    )
    executable_source = next(
        (candidate for candidate in executable_candidates if candidate.is_file()),
        packaged_executable,
    )
    sources = [executable_source, python_dll, Path(pywintypes.__file__)]
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(
                f"Service runtime file is missing: {source}. "
                "Run uv sync --all-packages --reinstall-package pywin32, then install again."
            )

    sources.extend(python_dll.parent.glob("vcruntime*.dll"))
    stable_abi_dll = python_dll.with_name("python3.dll")
    if stable_abi_dll.is_file():
        sources.append(stable_abi_dll)

    for source in sources:
        destination = host_directory / source.name
        if source.resolve() == destination.resolve():
            continue
        if destination.is_file() and filecmp.cmp(source, destination, shallow=False):
            continue
        shutil.copy2(source, destination)

    return installed_executable
