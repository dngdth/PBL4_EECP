from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_NETWORK_CATEGORIES = frozenset(
    {"generative_ai", "social_network", "vpn_proxy"}
)


@dataclass(frozen=True, slots=True)
class PolicySpecification:
    policy_hash: str
    profile: str
    version: int
    denied_applications: tuple[str, ...]
    blocked_categories: tuple[str, ...]
    usb_deny: bool


def parse_policy_payload(payload: dict[str, Any]) -> PolicySpecification:
    policy_hash = payload.get("policy_hash")
    rules = payload.get("rules")
    if not isinstance(policy_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", policy_hash):
        raise ValueError("policy_hash must be a SHA-256 hex digest")
    if payload.get("format", "eecp-policy/v1") != "eecp-policy/v1":
        raise ValueError("unsupported policy format")
    if not isinstance(rules, dict):
        raise ValueError("policy rules must be an object")

    applications = rules.get("applications", {})
    network = rules.get("network", {})
    devices = rules.get("devices", {})
    if not all(isinstance(item, dict) for item in (applications, network, devices)):
        raise ValueError("policy applications, network, and devices must be objects")

    denied = applications.get("deny", [])
    categories = network.get("block", network.get("blocked_categories", []))
    usb = devices.get("usb", devices.get("usb_storage", "allow"))
    if not isinstance(denied, list) or not all(isinstance(item, str) for item in denied):
        raise ValueError("applications.deny must be a string list")
    if not isinstance(categories, list) or not all(
        isinstance(item, str) for item in categories
    ):
        raise ValueError("network.block must be a string list")

    normalized_categories = tuple(item.strip().lower() for item in categories)
    unknown = sorted(set(normalized_categories) - SUPPORTED_NETWORK_CATEGORIES)
    if unknown:
        raise ValueError(f"unsupported network categories: {', '.join(unknown)}")
    if usb not in {"allow", "deny"}:
        raise ValueError("devices.usb must be allow or deny")

    version = payload.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError("policy version must be a positive integer")
    return PolicySpecification(
        policy_hash=policy_hash,
        profile=str(payload.get("profile", "")),
        version=version,
        denied_applications=tuple(_application_name(item) for item in denied),
        blocked_categories=normalized_categories,
        usb_deny=usb == "deny",
    )


def _application_name(value: str) -> str:
    normalized = value.split("(", 1)[0].strip().lower()
    if not normalized or Path(normalized).name != normalized or not normalized.endswith(".exe"):
        raise ValueError(f"invalid denied application name: {value}")
    return normalized
