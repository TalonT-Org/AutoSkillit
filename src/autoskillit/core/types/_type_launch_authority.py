"""Portable launch authority and provenance values.

This module is IL-0: it contains only immutable values and sibling type imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType

from ._type_execution_identity import BackendAuthorityKind
from ._type_launch_projection import LaunchContractError, _freeze_str_mapping

__all__ = [
    "BackendAuthority",
    "BackendAuthorityTier",
    "LaunchFallbackRoute",
    "LaunchSurface",
    "LaunchValueSource",
    "LaunchValueSourceKind",
    "ModelPinResolution",
    "ProviderBinding",
]


class LaunchSurface(StrEnum):
    """Semantic launch surfaces shared by CLI and MCP entrypoints."""

    HEADLESS_SKILL = "headless-skill"
    FLEET_OUTER = "fleet-outer"
    FLEET_STEP_NON_RESUMABLE = "fleet-step-non-resumable"
    INTERACTIVE_COOK = "interactive-cook"
    INTERACTIVE_ORDER = "interactive-order"


class BackendAuthorityTier(IntEnum):
    """Precedence of explicit backend authorities."""

    GLOBAL = 10
    RECIPE = 20
    STEP = 30
    CALLER = 40


_AUTHORITY_KIND_TIER = {
    BackendAuthorityKind.GLOBAL: BackendAuthorityTier.GLOBAL,
    BackendAuthorityKind.RECIPE: BackendAuthorityTier.RECIPE,
    BackendAuthorityKind.STEP: BackendAuthorityTier.STEP,
    BackendAuthorityKind.CALLER: BackendAuthorityTier.CALLER,
}


class LaunchValueSourceKind(StrEnum):
    """Portable provenance for provider, profile, model, and effort values."""

    DEFAULT = "default"
    GLOBAL = "global"
    RECIPE = "recipe"
    STEP = "step"
    CALLER = "caller"
    ADAPTER = "adapter"
    RESUME_BINDING = "resume-binding"


@dataclass(frozen=True, slots=True)
class BackendAuthority:
    """One explicit backend selection with typed tier and exact key path."""

    backend: str
    kind: BackendAuthorityKind
    tier: BackendAuthorityTier
    key_path: str

    def __post_init__(self) -> None:
        if not self.backend.strip():
            raise LaunchContractError("backend authority requires a backend")
        if not self.key_path.strip():
            raise LaunchContractError("backend authority requires a key path")
        expected = _AUTHORITY_KIND_TIER[self.kind]
        if self.tier is not expected:
            raise LaunchContractError(
                f"backend authority kind {self.kind.value!r} requires tier {expected.name}"
            )

    def to_payload(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "backend": self.backend,
                "kind": self.kind.value,
                "tier": self.tier.name.lower(),
                "key_path": self.key_path,
            }
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> BackendAuthority:
        """Strictly decode one canonical authority payload."""
        expected_fields = {"backend", "kind", "tier", "key_path"}
        if set(payload) != expected_fields or len(payload) != len(expected_fields):
            raise LaunchContractError("backend authority payload is not canonical")
        backend = payload["backend"]
        kind_value = payload["kind"]
        tier_value = payload["tier"]
        key_path = payload["key_path"]
        if not all(
            isinstance(value, str) for value in (backend, kind_value, tier_value, key_path)
        ):
            raise LaunchContractError("backend authority payload fields must be strings")
        assert isinstance(backend, str)
        assert isinstance(kind_value, str)
        assert isinstance(tier_value, str)
        assert isinstance(key_path, str)
        try:
            kind = BackendAuthorityKind(kind_value)
            tier = BackendAuthorityTier[tier_value.upper()]
        except (KeyError, ValueError) as exc:
            raise LaunchContractError("backend authority payload is invalid") from exc
        return cls(
            backend=backend,
            kind=kind,
            tier=tier,
            key_path=key_path,
        )


@dataclass(frozen=True, slots=True)
class LaunchValueSource:
    """Typed origin of a resolved launch value."""

    kind: LaunchValueSourceKind
    key_path: str

    def __post_init__(self) -> None:
        if not self.key_path.strip():
            raise LaunchContractError("launch value source requires a key path")

    def to_payload(self) -> Mapping[str, str]:
        return MappingProxyType({"kind": self.kind.value, "key_path": self.key_path})


@dataclass(frozen=True, slots=True)
class ModelPinResolution:
    """One resolved model value with its typed config origin."""

    model: str
    source: LaunchValueSource


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    """Explicit provider/profile binding resolved within a selected backend."""

    provider: str
    profile: str
    required_backend: str
    normalized_endpoint: str
    key_path: str
    provider_source: LaunchValueSource
    profile_source: LaunchValueSource
    endpoint_source: LaunchValueSource
    environment: Mapping[str, str] = field(default_factory=dict)
    secret_environment_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("provider", "profile", "required_backend", "key_path"):
            if not str(getattr(self, field_name)).strip():
                raise LaunchContractError(f"provider binding requires {field_name}")
        object.__setattr__(
            self, "environment", _freeze_str_mapping(self.environment, "provider environment")
        )
        keys = tuple(sorted(set(self.secret_environment_keys)))
        if any(not key for key in keys):
            raise LaunchContractError("secret environment keys must be non-empty")
        if set(keys) & set(self.environment):
            raise LaunchContractError("secret environment keys cannot be nonsecret environment")
        object.__setattr__(self, "secret_environment_keys", keys)


@dataclass(frozen=True, slots=True)
class LaunchFallbackRoute:
    """An explicitly authorized provider/model fallback on the selected backend."""

    backend: str
    provider: str
    profile: str
    model: str
    source: LaunchValueSource

    def to_payload(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "backend": self.backend,
                "provider": self.provider,
                "profile": self.profile,
                "model": self.model,
                "source": self.source.to_payload(),
            }
        )
