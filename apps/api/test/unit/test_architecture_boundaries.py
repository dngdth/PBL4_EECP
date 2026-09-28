import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
API_APP = ROOT / "apps" / "api" / "app"
AGENT = ROOT / "agent"
AGENT_CLIENT = AGENT / "client"
AGENT_SERVICE = AGENT / "service"
NAMED_PIPE_EXECUTOR = AGENT_CLIENT / "infrastructure" / "named_pipe_executor.py"
CONTRACTS = ROOT / "contracts"
WEB_FEATURES = ROOT / "apps" / "web" / "features"
LOCAL_GATEWAY = ROOT / "apps" / "gateway"


def _python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_backend_domain_has_no_outer_layer_dependencies() -> None:
    violations = []
    for path in (API_APP / "domain").rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith("app.") and not imported.startswith("app.domain"):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_backend_application_does_not_depend_on_outer_layers() -> None:
    forbidden = ("app.infrastructure", "app.presentation", "fastapi", "pydantic", "sqlite3")
    violations = []
    for path in (API_APP / "application").rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_agent_domain_and_application_depend_inward_only() -> None:
    violations = []
    boundaries = {
        "domain": ("agent.application", "agent.infrastructure"),
        "application": ("agent.infrastructure", "agent.service"),
    }
    for layer, forbidden in boundaries.items():
        for path in (AGENT / layer).rglob("*.py"):
            for imported in _python_imports(path):
                if imported.startswith(forbidden):
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_agent_client_does_not_depend_on_privileged_implementation() -> None:
    forbidden = (
        "agent.infrastructure.policy_enforcement",
        "agent.infrastructure.inprocess_executor",
        "agent.service",
    )
    violations = []
    for path in AGENT_CLIENT.rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_agent_service_has_no_backend_or_network_dependency() -> None:
    forbidden = (
        "agent.infrastructure.control_server",
        "fastapi",
        "http.server",
        "socket",
        "uvicorn",
    )
    violations = []
    for path in AGENT_SERVICE.rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_named_pipe_executor_has_only_client_side_boundary_dependencies() -> None:
    allowed_agent_imports = (
        "agent.application.privileged_execution",
        "agent.ipc",
    )
    violations = []
    for imported in _python_imports(NAMED_PIPE_EXECUTOR):
        if imported.startswith("agent.") and not imported.startswith(allowed_agent_imports):
            violations.append(f"{NAMED_PIPE_EXECUTOR.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_policy_command_processor_does_not_trigger_maintenance() -> None:
    path = AGENT / "application" / "policy_commands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "maintain"
    ]

    assert calls == []


def test_local_gateway_cannot_depend_on_privileged_or_backend_persistence() -> None:
    forbidden = (
        "agent.infrastructure.policy_enforcement",
        "agent.ipc.named_pipe_server",
        "app.infrastructure.persistence",
        "app.infrastructure.repositories",
        "sqlite3",
    )
    violations = []
    for path in LOCAL_GATEWAY.rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_agent_client_does_not_import_backend_fastapi_application() -> None:
    violations = []
    for path in AGENT_CLIENT.rglob("*.py"):
        for imported in _python_imports(path):
            if imported == "app" or imported.startswith("app."):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_backend_domain_does_not_import_gateway_websocket_runtime() -> None:
    violations = []
    for path in (API_APP / "domain").rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith("apps.gateway") or imported.startswith("fastapi"):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_shared_contracts_do_not_depend_on_runtime_layers() -> None:
    forbidden = ("app", "agent", "fastapi", "sqlite3")
    violations = []
    for path in CONTRACTS.rglob("*.py"):
        for imported in _python_imports(path):
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []


def test_frontend_features_do_not_depend_on_app_or_other_features() -> None:
    import_pattern = re.compile(r'from\s+["\'](@/[^"\']+)["\']')
    violations = []
    for path in WEB_FEATURES.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        own_feature = path.relative_to(WEB_FEATURES).parts[0]
        for imported in import_pattern.findall(path.read_text(encoding="utf-8")):
            if imported.startswith("@/app/"):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
                continue
            if imported.startswith("@/features/"):
                imported_feature = imported.split("/", 3)[2]
                if imported_feature != own_feature:
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")

    assert violations == []
