"""Immutable audit artifact reference value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ..closure_hashing import canonical_json_bytes, compute_canonical_hash
from ._type_audit_admission_validation import (
    _require_digest,
    _require_nonempty,
    _require_positive_int,
)

__all__ = ["ArtifactRef"]


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Hash identity plus an independently enforceable absolute locator policy."""

    locator: str = field(compare=False)
    media_type: str = field(compare=False)
    schema_version: int = field(compare=False)
    byte_size: int = field(compare=False)
    content_digest: str

    def __post_init__(self) -> None:
        _require_nonempty("ArtifactRef.locator", self.locator)
        locator = Path(self.locator)
        if not locator.is_absolute() or ".." in locator.parts:
            raise ValueError("ArtifactRef.locator must be an absolute non-traversing path")
        _require_nonempty("ArtifactRef.media_type", self.media_type)
        _require_positive_int("ArtifactRef.schema_version", self.schema_version)
        _require_positive_int("ArtifactRef.byte_size", self.byte_size, allow_zero=True)
        _require_digest("ArtifactRef.content_digest", self.content_digest)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def metadata_digest(self) -> str:
        return compute_canonical_hash(
            self.to_dict(),
            domain=f"autoskillit:audit-cycle:artifact-ref:v{self.schema_version}:sha256",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "content_digest": self.content_digest,
            "locator": self.locator,
            "media_type": self.media_type,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            return cls(
                locator=data["locator"],
                media_type=data["media_type"],
                schema_version=data["schema_version"],
                byte_size=data["byte_size"],
                content_digest=data["content_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid ArtifactRef: {exc}") from exc
