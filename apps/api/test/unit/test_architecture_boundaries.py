import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
API_APP = ROOT / "apps" / "api" / "app"
AGENT = ROOT / "agent"
WEB_FEATURES = ROOT / "apps" / "web" / "features"


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
        "application": ("agent.infrastructure",),
    }
    for layer, forbidden in boundaries.items():
        for path in (AGENT / layer).rglob("*.py"):
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
