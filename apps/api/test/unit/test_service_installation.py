import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.service import installation, windows_service


@pytest.fixture
def runtime_files(tmp_path, monkeypatch):
    environment = tmp_path / ".venv"
    environment.mkdir()
    host_directory = environment / "Scripts"
    host_directory.mkdir()
    python_directory = tmp_path / "python"
    python_directory.mkdir()
    package_directory = environment / "Lib" / "site-packages" / "win32"
    package_directory.mkdir(parents=True)
    sources = {
        "pythonservice.exe": package_directory / "pythonservice.exe",
        "python312.dll": python_directory / "python312.dll",
        "python3.dll": python_directory / "python3.dll",
        "vcruntime140.dll": python_directory / "vcruntime140.dll",
        "vcruntime140_1.dll": python_directory / "vcruntime140_1.dll",
        "pywintypes312.dll": package_directory / "pywintypes312.dll",
    }
    for name, path in sources.items():
        path.write_bytes(name.encode())
    monkeypatch.setattr(sys, "exec_prefix", str(environment))
    monkeypatch.setattr(sys, "executable", str(host_directory / "python.exe"))
    monkeypatch.setattr(sys, "dllhandle", 123, raising=False)
    monkeypatch.setitem(
        sys.modules, "pywintypes",
        SimpleNamespace(__file__=str(sources["pywintypes312.dll"])),
    )
    monkeypatch.setitem(
        sys.modules, "win32api",
        SimpleNamespace(GetModuleFileName=lambda handle: str(sources["python312.dll"])),
    )
    monkeypatch.setitem(
        sys.modules, "win32service",
        SimpleNamespace(__file__=str(package_directory / "win32service.pyd")),
    )
    return host_directory, sources


def test_service_host_has_runtime_dlls_beside_executable(runtime_files, monkeypatch):
    environment, sources = runtime_files

    executable = installation.prepare_service_executable()

    assert executable == environment / "pythonservice.exe"
    for name, source in sources.items():
        assert (environment / name).read_bytes() == source.read_bytes()
    assert sources["pythonservice.exe"].is_file()

    # Reinstalling identical files must also work while Windows has them loaded.
    monkeypatch.setattr(
        installation.shutil, "copy2",
        lambda *args: pytest.fail("unchanged runtime files should not be overwritten"),
    )
    assert installation.prepare_service_executable() == executable


def test_service_host_repairs_runtime_after_older_installer_moved_executable(runtime_files):
    environment, sources = runtime_files
    sources["pythonservice.exe"].rename(environment.parent / "pythonservice.exe")

    assert installation.prepare_service_executable().is_file()
    assert (environment / "python312.dll").read_bytes() == b"python312.dll"
    assert (environment / "pywintypes312.dll").read_bytes() == b"pywintypes312.dll"


def test_missing_runtime_is_reported_before_staging_any_files(runtime_files):
    environment, sources = runtime_files
    sources["pywintypes312.dll"].unlink()

    with pytest.raises(FileNotFoundError, match="reinstall-package pywin32"):
        installation.prepare_service_executable()

    assert not (environment / "pythonservice.exe").exists()


@pytest.mark.parametrize("command", ["install", "start"])
def test_scm_registers_absolute_import_root_and_stages_only_on_install(monkeypatch, command):
    executable = Path("service-host") / "pythonservice.exe"
    prepared = []
    handled = []

    def prepare():
        prepared.append(True)
        return executable

    def handle(service_class, **kwargs):
        handled.append((service_class, kwargs))

    monkeypatch.setattr(windows_service, "_require_pywin32", lambda: None)
    monkeypatch.setattr(installation, "prepare_service_executable", prepare)
    monkeypatch.setattr(
        windows_service, "win32serviceutil", SimpleNamespace(HandleCommandLine=handle)
    )
    monkeypatch.setattr(windows_service.EECPWindowsService, "_exe_name_", None, raising=False)

    windows_service.run_scm_command(command)

    project_root = Path(windows_service.__file__).resolve().parents[2]
    assert handled[0][1]["serviceClassString"] == (
        f"{project_root}\\agent.service.windows_service.EECPWindowsService"
    )
    assert handled[0][1]["argv"][1] == command
    assert bool(prepared) == (command == "install")
    if command == "install":
        assert windows_service.EECPWindowsService._exe_name_ == str(executable)
