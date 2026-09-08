from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.domain.exceptions.errors import PolicyValidationError

DEFAULT_POLICY_PROFILE = "INTERNET_NO_AI"


@dataclass(frozen=True, slots=True)
class PolicyProfileDefinition:
    id: str
    label: str
    description: str
    rules: dict[str, Any]
    is_builtin: bool = False

    def __post_init__(self) -> None:
        normalized_id = self.id.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", normalized_id):
            raise PolicyValidationError(
                "profile id must contain 2-64 uppercase letters, digits, or underscores"
            )
        label = self.label.strip()
        description = self.description.strip()
        if not label or len(label) > 100:
            raise PolicyValidationError("profile label must contain 1-100 characters")
        if not description or len(description) > 500:
            raise PolicyValidationError(
                "profile description must contain 1-500 characters"
            )
        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "rules", _normalize_rules(self.rules))

    def editable_copy(
        self,
        *,
        label: str,
        description: str,
        rules: dict[str, Any],
    ) -> PolicyProfileDefinition:
        if self.is_builtin:
            raise PolicyValidationError("built-in policy profiles are read-only")
        return PolicyProfileDefinition(
            id=self.id,
            label=label,
            description=description,
            rules=rules,
            is_builtin=False,
        )


class PolicyProfileCatalog:
    """Domain catalog of policy choices offered to teachers."""

    def __init__(self, profiles: tuple[PolicyProfileDefinition, ...]):
        self._profiles = profiles
        self._by_id = {profile.id: profile for profile in profiles}

    def get(self, profile_id: str) -> PolicyProfileDefinition:
        normalized = profile_id.strip().upper()
        try:
            profile = self._by_id[normalized]
        except KeyError as exc:
            raise PolicyValidationError(
                f"unknown policy profile: {profile_id}"
            ) from exc
        return PolicyProfileDefinition(
            id=profile.id,
            label=profile.label,
            description=profile.description,
            rules=deepcopy(profile.rules),
            is_builtin=profile.is_builtin,
        )

    def list_all(self) -> list[PolicyProfileDefinition]:
        return [self.get(profile.id) for profile in self._profiles]


def _normalize_rules(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyValidationError("policy rules must be an object")
    unknown_sections = sorted(set(value) - {"applications", "network", "devices"})
    if unknown_sections:
        raise PolicyValidationError(
            f"unknown policy rule sections: {', '.join(unknown_sections)}"
        )

    normalized: dict[str, Any] = {}
    if "applications" in value:
        applications = _mapping(value["applications"], "applications")
        unknown = sorted(set(applications) - {"allow", "deny"})
        if unknown:
            raise PolicyValidationError(
                f"unknown applications rules: {', '.join(unknown)}"
            )
        allowed = _string_list(applications.get("allow", []), "applications.allow")
        denied = _string_list(applications.get("deny", []), "applications.deny")
        overlap = sorted(set(allowed) & set(denied))
        if overlap:
            raise PolicyValidationError(
                f"applications cannot be both allowed and denied: {', '.join(overlap)}"
            )
        normalized["applications"] = {"allow": allowed, "deny": denied}

    if "network" in value:
        network = _mapping(value["network"], "network")
        unknown = sorted(set(network) - {"block"})
        if unknown:
            raise PolicyValidationError(f"unknown network rules: {', '.join(unknown)}")
        normalized["network"] = {
            "block": _string_list(network.get("block", []), "network.block")
        }

    if "devices" in value:
        devices = _mapping(value["devices"], "devices")
        unknown = sorted(set(devices) - {"usb"})
        if unknown:
            raise PolicyValidationError(f"unknown device rules: {', '.join(unknown)}")
        usb = devices.get("usb", "allow")
        if usb not in {"allow", "deny"}:
            raise PolicyValidationError("devices.usb must be allow or deny")
        normalized["devices"] = {"usb": usb}

    return normalized


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyValidationError(f"{field} must be an object")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PolicyValidationError(f"{field} must be a list of strings")
    normalized = [item.strip().lower() for item in value]
    if any(not item for item in normalized):
        raise PolicyValidationError(f"{field} cannot contain empty values")
    if len(normalized) != len(set(normalized)):
        raise PolicyValidationError(f"{field} values must be unique")
    return normalized


BUILT_IN_POLICY_PROFILES = PolicyProfileCatalog(
    (
        PolicyProfileDefinition(
            id=DEFAULT_POLICY_PROFILE,
            label="Internet + No AI",
            description=(
                "Allow programming tools and general Internet access while blocking "
                "generative AI, social networks, remote-control software, and USB storage."
            ),
            rules={
                "applications": {
                    "allow": ["vscode.exe", "gcc.exe"],
                    "deny": ["chatgpt.exe", "anydesk.exe", "teamviewer.exe"],
                },
                "network": {"block": ["generative_ai", "social_network"]},
                "devices": {"usb": "deny"},
            },
            is_builtin=True,
        ),
        PolicyProfileDefinition(
            id="OFFLINE_PROGRAMMING",
            label="Offline Programming",
            description=(
                "Allow local programming tools while blocking Internet categories, "
                "remote-control software, and USB storage."
            ),
            rules={
                "applications": {
                    "allow": ["vscode.exe", "gcc.exe"],
                    "deny": [
                        "chrome.exe",
                        "msedge.exe",
                        "firefox.exe",
                        "anydesk.exe",
                        "teamviewer.exe",
                    ],
                },
                "network": {
                    "block": ["generative_ai", "social_network", "vpn_proxy"]
                },
                "devices": {"usb": "deny"},
            },
            is_builtin=True,
        ),
    )
)
