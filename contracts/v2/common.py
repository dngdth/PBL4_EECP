from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, JsonValue, StringConstraints

PROTOCOL_VERSION = 2
ProtocolVersion = Literal[2]
OpaqueId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255, strict=True),
]
Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$", strict=True),
]
JsonObject = dict[str, JsonValue]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]


class ContractModel(BaseModel):
    """Strict immutable base for Protocol v2 JSON contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


class VersionedContract(ContractModel):
    protocol_version: ProtocolVersion
