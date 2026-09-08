from typing import Any


def render_policy_yaml(profile: str, rules: dict[str, Any]) -> str:
    """Render the EECP policy preview at the HTTP presentation boundary."""
    lines = ["policy:", f"  profile: {_yaml_scalar(profile)}", "  rules:"]
    lines.extend(_yaml_lines(rules, indent=4))
    return "\n".join(lines) + "\n"


def _yaml_lines(value: dict[str, Any], indent: int) -> list[str]:
    lines: list[str] = []
    prefix = " " * indent
    for key, item in value.items():
        if isinstance(item, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(_yaml_lines(item, indent + 2))
        elif isinstance(item, list):
            lines.append(f"{prefix}{key}:")
            lines.extend(f"{' ' * (indent + 2)}- {_yaml_scalar(entry)}" for entry in item)
        else:
            lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
    return lines


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)
