from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from contracts.v2.common import (
    ContractModel,
    OpaqueId,
    Sha256Hex,
    UtcDatetime,
    VersionedContract,
)

RuleValue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255, strict=True),
]
SignatureValue = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8192, strict=True),
]


class NetworkCategory(StrEnum):
    GENERATIVE_AI = "generative_ai"
    SOCIAL_NETWORK = "social_network"
    VPN_PROXY = "vpn_proxy"


class UsbAccess(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ApplicationRules(ContractModel):
    allow: tuple[RuleValue, ...] | None = None
    deny: tuple[RuleValue, ...] | None = None

    @model_validator(mode="after")
    def validate_lists(self) -> ApplicationRules:
        allowed = self.allow or ()
        denied = self.deny or ()
        if len(allowed) != len(set(allowed)) or len(denied) != len(set(denied)):
            raise ValueError("application rule values must be unique")
        overlap = set(allowed) & set(denied)
        if overlap:
            raise ValueError("applications cannot be both allowed and denied")
        return self


class NetworkRules(ContractModel):
    block: tuple[NetworkCategory, ...] | None = None
    blocked_categories: tuple[NetworkCategory, ...] | None = None
    allow_domains: tuple[RuleValue, ...] | None = None
    blocked_domains: tuple[RuleValue, ...] | None = None
    blocked_ips: tuple[RuleValue, ...] | None = None
    blocked_cidrs: tuple[RuleValue, ...] | None = None

    @model_validator(mode="after")
    def validate_unique_categories(self) -> NetworkRules:
        if self.block is not None and self.blocked_categories is not None:
            raise ValueError("use network.block or legacy blocked_categories, not both")
        categories = self.block if self.block is not None else self.blocked_categories or ()
        if len(categories) != len(set(categories)):
            raise ValueError("network category values must be unique")
        domains = self.allow_domains or ()
        blocked_domains = self.blocked_domains or ()
        for field, values in (
            ("allow_domains", domains),
            ("blocked_domains", blocked_domains),
            ("blocked_ips", self.blocked_ips or ()),
            ("blocked_cidrs", self.blocked_cidrs or ()),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"network.{field} values must be unique")
        for value in self.blocked_ips or ():
            try:
                ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError(f"invalid blocked IP address: {value}") from exc
        for value in self.blocked_cidrs or ():
            try:
                ipaddress.ip_network(value, strict=True)
            except ValueError as exc:
                raise ValueError(f"invalid blocked CIDR network: {value}") from exc
        return self


class DeviceRules(ContractModel):
    usb: UsbAccess | None = None
    usb_storage: UsbAccess | None = None

    @model_validator(mode="after")
    def validate_usb_vocabulary(self) -> DeviceRules:
        if self.usb is not None and self.usb_storage is not None:
            raise ValueError("use devices.usb or legacy usb_storage, not both")
        return self


class PolicyRules(ContractModel):
    applications: ApplicationRules | None = None
    network: NetworkRules | None = None
    devices: DeviceRules | None = None


def canonical_policy_json(
    policy_id: str,
    policy_version: int,
    rules: PolicyRules,
) -> str:
    """Return the exact canonical JSON used by the current PolicyDocument hash."""

    payload = {
        "profile": policy_id.strip().upper(),
        "rules": rules.model_dump(mode="json", exclude_none=True),
        "version": policy_version,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_policy_hash(
    policy_id: str,
    policy_version: int,
    rules: PolicyRules,
) -> str:
    return hashlib.sha256(
        canonical_policy_json(policy_id, policy_version, rules).encode("utf-8")
    ).hexdigest()


def compute_policy_signature(
    signing_key: str,
    policy_id: str,
    policy_version: int,
    rules: PolicyRules,
    session_id: str,
) -> str:
    if not signing_key:
        raise ValueError("policy signing key must not be empty")
    content = f"{canonical_policy_json(policy_id, policy_version, rules)}|{session_id}"
    digest = hmac.new(signing_key.encode(), content.encode(), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def verify_policy_signature(policy: PolicyEnvelope, verification_key: str) -> bool:
    if policy.signature is None or not verification_key:
        return False
    expected = compute_policy_signature(
        verification_key,
        policy.policy_id,
        policy.policy_version,
        policy.rules,
        policy.session_id,
    )
    return hmac.compare_digest(policy.signature, expected)


class PolicyEnvelope(VersionedContract):
    policy_id: OpaqueId
    policy_version: int = Field(strict=True, ge=1)
    policy_hash: Sha256Hex
    session_id: OpaqueId
    issued_at: UtcDatetime
    expires_at: UtcDatetime | None = None
    rules: PolicyRules
    signature: SignatureValue | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> PolicyEnvelope:
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        expected = compute_policy_hash(self.policy_id, self.policy_version, self.rules)
        if self.policy_hash != expected:
            raise ValueError("policy_hash does not match the canonical policy content")
        return self

    @classmethod
    def from_legacy(
        cls,
        *,
        policy_id: str,
        policy_version: int,
        policy_hash: str,
        session_id: str,
        issued_at: datetime,
        rules: dict[str, object],
        expires_at: datetime | None = None,
        signature: str | None = None,
    ) -> PolicyEnvelope:
        return cls(
            protocol_version=2,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            session_id=session_id,
            issued_at=issued_at,
            expires_at=expires_at,
            rules=PolicyRules.model_validate(rules),
            signature=signature,
        )
