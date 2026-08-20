"""Portable launch authority and stable launch-contract values.

This module is IL-0: it contains only immutable values and structural protocols.
Backend selection and physical finalization live in the execution layer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType

from ._type_backend import CmdOrigin, CmdSpec
from ._type_execution_identity import BackendAuthorityKind
from ._type_launch_projection import (
    LaunchContractError,
    SkillProjectionBinding,
    _freeze_metadata,
    _freeze_str_mapping,
    _json_value,
    _payload_value,
)

__all__ = [
    "CANONICAL_LAUNCH_DIGEST_FIELDS",
    "LAUNCH_CONTRACT_SCHEMA_VERSION",
    "BackendAuthority",
    "BackendAuthorityTier",
    "LaunchAdapterResult",
    "LaunchContractError",
    "LaunchFallbackRoute",
    "LaunchPreparation",
    "LaunchResolutionRequest",
    "LaunchSurface",
    "LaunchValueSource",
    "LaunchValueSourceKind",
    "ModelPinResolution",
    "ProviderBinding",
    "ResolvedLaunchContract",
    "SecretEnvironmentBinding",
    "SemanticLaunchPlan",
    "SkillProjectionBinding",
]


LAUNCH_CONTRACT_SCHEMA_VERSION = 3

# Top-level field order is part of the persisted stable-digest schema. Runtime
# observations, attempt counters, retry state, and resume state do not belong here.
CANONICAL_LAUNCH_DIGEST_FIELDS = (
    "schema_version",
    "surface",
    "selected_backend",
    "effective_backend",
    "backend_authority",
    "provider",
    "profile",
    "normalized_endpoint",
    "provider_source",
    "profile_source",
    "endpoint_source",
    "models",
    "effort",
    "effort_source",
    "semantic_digest",
    "adapter_digest",
    "projection_digest",
    "skill_projection_binding",
    "fallback_routes",
    "cwd",
    "command",
    "executable_identity",
    "plugin_identity",
    "branch_identity",
    "worktree_identity",
    "projection_identity",
    "artifact_paths",
    "nonsecret_env",
    "secret_bindings",
    "quota_identity",
)


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
    """One resolved model value with its typed config origin.

    Gives the model axis the provenance the backend axis already carries,
    reusing the LaunchValueSource wrapper defined beside it rather than
    BackendPinResolution's flat NamedTuple shape.
    """

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


@dataclass(frozen=True, slots=True)
class SemanticLaunchPlan:
    """Backend-neutral semantics fixed before adapter command construction."""

    surface: LaunchSurface
    semantic_digest: str
    projection_digest: str

    def __post_init__(self) -> None:
        if not self.semantic_digest or not self.projection_digest:
            raise LaunchContractError("semantic and projection digests are required")


@dataclass(frozen=True, slots=True)
class LaunchResolutionRequest:
    """All declared launch inputs; only authority_candidates may select backend."""

    surface: LaunchSurface
    authority_candidates: tuple[BackendAuthority, ...]
    semantic_plan: SemanticLaunchPlan
    command: str
    arguments: tuple[str, ...]
    cwd: str
    requested_model: str | None
    requested_model_source: LaunchValueSource
    configured_model: str | None
    configured_model_source: LaunchValueSource
    effort: str | None
    effort_source: LaunchValueSource
    sandbox_mode: str
    network_access: bool
    pty_required: bool
    inherited_fd_policy: str
    branch_identity: Mapping[str, str]
    worktree_identity: Mapping[str, str]
    executable_identity: Mapping[str, str]
    plugin_identity: Mapping[str, str]
    projection_identity: Mapping[str, str]
    artifact_paths: tuple[str, ...]
    quota_identity: Mapping[str, str]
    provider_binding: ProviderBinding | None = None
    skill_projection_binding: SkillProjectionBinding | None = None
    fallback_routes: tuple[LaunchFallbackRoute, ...] = ()
    non_authority_metadata: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.command or not self.cwd:
            raise LaunchContractError("launch command and cwd are required")
        object.__setattr__(self, "authority_candidates", tuple(self.authority_candidates))
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "fallback_routes", tuple(self.fallback_routes))
        object.__setattr__(self, "artifact_paths", tuple(self.artifact_paths))
        for field_name in (
            "branch_identity",
            "worktree_identity",
            "executable_identity",
            "plugin_identity",
            "projection_identity",
            "quota_identity",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_str_mapping(getattr(self, field_name), field_name.replace("_", " ")),
            )
        object.__setattr__(
            self, "non_authority_metadata", _freeze_metadata(self.non_authority_metadata)
        )


@dataclass(frozen=True, slots=True)
class LaunchPreparation:
    """Non-executable launch authority fixed before one adapter build."""

    surface: LaunchSurface
    selected_backend: str
    effective_backend: str
    backend_authority: BackendAuthority
    semantic_plan: SemanticLaunchPlan
    command: str
    arguments: tuple[str, ...]
    cwd: str
    provider: str
    profile: str
    normalized_endpoint: str
    provider_source: LaunchValueSource
    profile_source: LaunchValueSource
    endpoint_source: LaunchValueSource
    requested_model: str | None
    requested_model_source: LaunchValueSource
    configured_model: str | None
    configured_model_source: LaunchValueSource
    effort: str | None
    effort_source: LaunchValueSource
    fallback_routes: tuple[LaunchFallbackRoute, ...]
    sandbox_mode: str
    network_access: bool
    pty_required: bool
    inherited_fd_policy: str
    branch_identity: Mapping[str, str]
    worktree_identity: Mapping[str, str]
    executable_identity: Mapping[str, str]
    plugin_identity: Mapping[str, str]
    projection_identity: Mapping[str, str]
    artifact_paths: tuple[str, ...]
    quota_identity: Mapping[str, str]
    provider_environment: Mapping[str, str]
    secret_environment_keys: tuple[str, ...]
    secret_profile_identity: str
    skill_projection_binding: SkillProjectionBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "fallback_routes", tuple(self.fallback_routes))
        object.__setattr__(self, "artifact_paths", tuple(self.artifact_paths))
        object.__setattr__(self, "secret_environment_keys", tuple(self.secret_environment_keys))
        for field_name in (
            "branch_identity",
            "worktree_identity",
            "executable_identity",
            "plugin_identity",
            "projection_identity",
            "quota_identity",
            "provider_environment",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_str_mapping(getattr(self, field_name), field_name.replace("_", " ")),
            )


@dataclass(frozen=True, slots=True)
class LaunchAdapterResult:
    """One backend adapter's physical command and echoed semantic authority."""

    backend: str
    provider: str
    profile: str
    normalized_endpoint: str
    physical_model: str | None
    physical_model_source: LaunchValueSource
    effort: str | None
    effort_source: LaunchValueSource
    semantic_digest: str
    adapter_digest: str
    projection_digest: str
    cwd: str
    command: str
    arguments: tuple[str, ...]
    branch_identity: Mapping[str, str]
    worktree_identity: Mapping[str, str]
    executable_identity: Mapping[str, str]
    plugin_identity: Mapping[str, str]
    projection_identity: Mapping[str, str]
    artifact_paths: tuple[str, ...]
    nonsecret_env: Mapping[str, str]
    cmd_spec: CmdSpec
    secret_environment_keys: tuple[str, ...] = ()
    secret_profile_identity: str = ""
    unsupported_reason: str | None = None
    skill_projection_binding: SkillProjectionBinding | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "artifact_paths", tuple(self.artifact_paths))
        object.__setattr__(
            self,
            "secret_environment_keys",
            tuple(sorted(set(self.secret_environment_keys))),
        )
        for field_name in (
            "branch_identity",
            "worktree_identity",
            "executable_identity",
            "plugin_identity",
            "projection_identity",
            "nonsecret_env",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_str_mapping(getattr(self, field_name), field_name.replace("_", " ")),
            )


@dataclass(frozen=True, slots=True)
class SecretEnvironmentBinding:
    """Secret identity and digest retained without retaining its live value."""

    environment_key: str
    profile_identity: str
    value_sha256: str

    def __post_init__(self) -> None:
        if not self.environment_key or not self.profile_identity:
            raise LaunchContractError("secret binding requires key and profile identity")
        if len(self.value_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.value_sha256
        ):
            raise LaunchContractError("secret binding digest must be lowercase sha256")

    def to_payload(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "environment_key": self.environment_key,
                "profile_identity": self.profile_identity,
                "value_sha256": self.value_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class ResolvedLaunchContract:
    """Secret-free, immutable, stable physical launch authority."""

    surface: LaunchSurface
    selected_backend: str
    effective_backend: str
    backend_authority: BackendAuthority
    provider: str
    profile: str
    normalized_endpoint: str
    provider_source: LaunchValueSource
    profile_source: LaunchValueSource
    endpoint_source: LaunchValueSource
    requested_model: str | None
    requested_model_source: LaunchValueSource
    configured_model: str | None
    configured_model_source: LaunchValueSource
    physical_model: str | None
    physical_model_source: LaunchValueSource
    effort: str | None
    effort_source: LaunchValueSource
    semantic_digest: str
    adapter_digest: str
    projection_digest: str
    fallback_routes: tuple[LaunchFallbackRoute, ...]
    cwd: str
    cmd_spec: CmdSpec
    sandbox_mode: str
    network_access: bool
    pty_required: bool
    inherited_fd_policy: str
    inherited_fd_count: int
    executable_identity: Mapping[str, str]
    plugin_identity: Mapping[str, str]
    branch_identity: Mapping[str, str]
    worktree_identity: Mapping[str, str]
    projection_identity: Mapping[str, str]
    artifact_paths: tuple[str, ...]
    nonsecret_env: Mapping[str, str]
    secret_bindings: tuple[SecretEnvironmentBinding, ...]
    quota_identity: Mapping[str, str]
    skill_projection_binding: SkillProjectionBinding | None = None
    schema_version: int = field(default=LAUNCH_CONTRACT_SCHEMA_VERSION, init=False)
    _canonical_payload: Mapping[str, object] = field(init=False, repr=False, compare=False)
    _canonical_json: str = field(init=False, repr=False, compare=False)
    _digest: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != LAUNCH_CONTRACT_SCHEMA_VERSION:
            raise LaunchContractError("unsupported launch contract schema version")
        if (
            self.skill_projection_binding is not None
            and not self.skill_projection_binding.finalized
        ):
            raise LaunchContractError(
                "resolved launch contract requires a finalized skill projection binding"
            )
        object.__setattr__(self, "fallback_routes", tuple(self.fallback_routes))
        object.__setattr__(self, "artifact_paths", tuple(self.artifact_paths))
        object.__setattr__(self, "secret_bindings", tuple(self.secret_bindings))
        for field_name in (
            "executable_identity",
            "plugin_identity",
            "branch_identity",
            "worktree_identity",
            "projection_identity",
            "nonsecret_env",
            "quota_identity",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_str_mapping(getattr(self, field_name), field_name.replace("_", " ")),
            )

        origin = self.cmd_spec.origin
        origin_payload: Mapping[str, object] | None = None
        if origin is not None:
            origin_payload = {
                "binary": origin.binary,
                "mode_flags": origin.mode_flags,
                "kv_flags": origin.kv_flags,
                "positional": origin.positional,
                "variadic_pairs": origin.variadic_pairs,
            }
        payload = {
            "schema_version": self.schema_version,
            "surface": self.surface.value,
            "selected_backend": self.selected_backend,
            "effective_backend": self.effective_backend,
            "backend_authority": self.backend_authority.to_payload(),
            "provider": self.provider,
            "profile": self.profile,
            "normalized_endpoint": self.normalized_endpoint,
            "provider_source": self.provider_source.to_payload(),
            "profile_source": self.profile_source.to_payload(),
            "endpoint_source": self.endpoint_source.to_payload(),
            "models": self.models,
            "effort": self.effort,
            "effort_source": self.effort_source.to_payload(),
            "semantic_digest": self.semantic_digest,
            "adapter_digest": self.adapter_digest,
            "projection_digest": self.projection_digest,
            "skill_projection_binding": (
                self.skill_projection_binding.canonical_payload
                if self.skill_projection_binding is not None
                else None
            ),
            "fallback_routes": tuple(route.to_payload() for route in self.fallback_routes),
            "cwd": self.cwd,
            "command": {
                "argv": self.cmd_spec.cmd,
                "origin": origin_payload,
                "process_idle_timeout_ms": self.cmd_spec.process_idle_timeout_ms,
                "force_inactive_agent_teams": self.cmd_spec.force_inactive_agent_teams,
                "sandbox_mode": self.sandbox_mode,
                "network_access": self.network_access,
                "pty_required": self.pty_required,
                "fd_policy": self.inherited_fd_policy,
                "inherited_fd_count": self.inherited_fd_count,
            },
            "executable_identity": self.executable_identity,
            "plugin_identity": self.plugin_identity,
            "branch_identity": self.branch_identity,
            "worktree_identity": self.worktree_identity,
            "projection_identity": self.projection_identity,
            "artifact_paths": self.artifact_paths,
            "nonsecret_env": self.nonsecret_env,
            "secret_bindings": tuple(binding.to_payload() for binding in self.secret_bindings),
            "quota_identity": self.quota_identity,
        }
        if tuple(payload) != CANONICAL_LAUNCH_DIGEST_FIELDS:
            raise AssertionError("canonical launch payload fields drifted from schema")
        canonical_payload = _payload_value(payload)
        assert isinstance(canonical_payload, Mapping)
        canonical_json = json.dumps(
            _json_value(canonical_payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        from hashlib import sha256

        object.__setattr__(self, "_canonical_payload", canonical_payload)
        object.__setattr__(self, "_canonical_json", canonical_json)
        object.__setattr__(self, "_digest", sha256(canonical_json.encode()).hexdigest())

    @property
    def models(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "requested": self.requested_model,
                "requested_source": self.requested_model_source.to_payload(),
                "configured": self.configured_model,
                "configured_source": self.configured_model_source.to_payload(),
                "adapter_physical": self.physical_model,
                "adapter_physical_source": self.physical_model_source.to_payload(),
            }
        )

    @property
    def canonical_payload(self) -> Mapping[str, object]:
        return self._canonical_payload

    @property
    def canonical_json(self) -> str:
        return self._canonical_json

    @property
    def digest(self) -> str:
        return self._digest

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, object],
        *,
        expected_digest: str | None = None,
    ) -> ResolvedLaunchContract:
        """Rehydrate one typed contract and prove its complete canonical identity."""

        def require_mapping(value: object, field_name: str) -> Mapping[str, object]:
            if not isinstance(value, Mapping):
                raise LaunchContractError(f"{field_name} must be an object")
            return value

        def require_str_mapping(value: object, field_name: str) -> dict[str, str]:
            mapping = require_mapping(value, field_name)
            if any(
                not isinstance(key, str) or not isinstance(item, str)
                for key, item in mapping.items()
            ):
                raise LaunchContractError(f"{field_name} must map strings to strings")
            return {str(key): str(item) for key, item in mapping.items()}

        def require_sequence(value: object, field_name: str) -> tuple[object, ...]:
            if not isinstance(value, (list, tuple)):
                raise LaunchContractError(f"{field_name} must be an array")
            return tuple(value)

        def require_int(value: object, field_name: str) -> int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise LaunchContractError(f"{field_name} must be an integer")
            return value

        def source(value: object, field_name: str) -> LaunchValueSource:
            mapping = require_mapping(value, field_name)
            return LaunchValueSource(
                kind=LaunchValueSourceKind(str(mapping["kind"])),
                key_path=str(mapping["key_path"]),
            )

        try:
            field_order = tuple(payload)
            if field_order not in {
                CANONICAL_LAUNCH_DIGEST_FIELDS,
                tuple(sorted(CANONICAL_LAUNCH_DIGEST_FIELDS)),
            }:
                raise LaunchContractError(
                    "launch contract fields do not match the canonical schema"
                )
            if (
                require_int(payload["schema_version"], "schema version")
                != LAUNCH_CONTRACT_SCHEMA_VERSION
            ):
                raise LaunchContractError("unsupported launch contract schema version")

            authority_payload = require_mapping(payload["backend_authority"], "backend authority")
            authority = BackendAuthority(
                backend=str(authority_payload["backend"]),
                kind=BackendAuthorityKind(str(authority_payload["kind"])),
                tier=BackendAuthorityTier[str(authority_payload["tier"]).upper()],
                key_path=str(authority_payload["key_path"]),
            )
            models = require_mapping(payload["models"], "models")
            command = require_mapping(payload["command"], "command")
            origin_payload = command.get("origin")
            origin: CmdOrigin | None = None
            if origin_payload is not None:
                origin_mapping = require_mapping(origin_payload, "command origin")
                origin = CmdOrigin(
                    binary=str(origin_mapping["binary"]),
                    mode_flags=tuple(
                        str(item)
                        for item in require_sequence(
                            origin_mapping["mode_flags"], "command origin mode flags"
                        )
                    ),
                    kv_flags=tuple(
                        (str(pair[0]), str(pair[1]))
                        for item in require_sequence(
                            origin_mapping["kv_flags"], "command origin key/value flags"
                        )
                        for pair in (require_sequence(item, "command origin key/value flag"),)
                        if len(pair) == 2
                    ),
                    positional=tuple(
                        str(item)
                        for item in require_sequence(
                            origin_mapping["positional"], "command origin positional arguments"
                        )
                    ),
                    variadic_pairs=tuple(
                        (str(pair[0]), str(pair[1]))
                        for item in require_sequence(
                            origin_mapping["variadic_pairs"], "command origin variadic pairs"
                        )
                        for pair in (require_sequence(item, "command origin variadic pair"),)
                        if len(pair) == 2
                    ),
                )
            fallback_routes = tuple(
                LaunchFallbackRoute(
                    backend=str(route["backend"]),
                    provider=str(route["provider"]),
                    profile=str(route["profile"]),
                    model=str(route["model"]),
                    source=source(route["source"], "fallback route source"),
                )
                for item in require_sequence(payload["fallback_routes"], "fallback routes")
                for route in (require_mapping(item, "fallback route"),)
            )
            secret_bindings = tuple(
                SecretEnvironmentBinding(
                    environment_key=str(binding["environment_key"]),
                    profile_identity=str(binding["profile_identity"]),
                    value_sha256=str(binding["value_sha256"]),
                )
                for item in require_sequence(payload["secret_bindings"], "secret bindings")
                for binding in (require_mapping(item, "secret binding"),)
            )
            cwd = str(payload["cwd"])
            nonsecret_env = require_str_mapping(payload["nonsecret_env"], "nonsecret env")
            contract = cls(
                surface=LaunchSurface(str(payload["surface"])),
                selected_backend=str(payload["selected_backend"]),
                effective_backend=str(payload["effective_backend"]),
                backend_authority=authority,
                provider=str(payload["provider"]),
                profile=str(payload["profile"]),
                normalized_endpoint=str(payload["normalized_endpoint"]),
                provider_source=source(payload["provider_source"], "provider source"),
                profile_source=source(payload["profile_source"], "profile source"),
                endpoint_source=source(payload["endpoint_source"], "endpoint source"),
                requested_model=(
                    str(models["requested"]) if models["requested"] is not None else None
                ),
                requested_model_source=source(
                    models["requested_source"], "requested model source"
                ),
                configured_model=(
                    str(models["configured"]) if models["configured"] is not None else None
                ),
                configured_model_source=source(
                    models["configured_source"], "configured model source"
                ),
                physical_model=(
                    str(models["adapter_physical"])
                    if models["adapter_physical"] is not None
                    else None
                ),
                physical_model_source=source(
                    models["adapter_physical_source"], "physical model source"
                ),
                effort=(str(payload["effort"]) if payload["effort"] is not None else None),
                effort_source=source(payload["effort_source"], "effort source"),
                semantic_digest=str(payload["semantic_digest"]),
                adapter_digest=str(payload["adapter_digest"]),
                projection_digest=str(payload["projection_digest"]),
                skill_projection_binding=(
                    SkillProjectionBinding.from_payload(
                        require_mapping(
                            payload["skill_projection_binding"],
                            "skill projection binding",
                        )
                    )
                    if payload["skill_projection_binding"] is not None
                    else None
                ),
                fallback_routes=fallback_routes,
                cwd=cwd,
                cmd_spec=CmdSpec(
                    cmd=tuple(
                        str(item) for item in require_sequence(command["argv"], "command argv")
                    ),
                    env=nonsecret_env,
                    cwd=cwd,
                    origin=origin,
                    process_idle_timeout_ms=require_int(
                        command["process_idle_timeout_ms"],
                        "command process idle timeout",
                    ),
                    force_inactive_agent_teams=bool(command["force_inactive_agent_teams"]),
                ),
                sandbox_mode=str(command["sandbox_mode"]),
                network_access=bool(command["network_access"]),
                pty_required=bool(command["pty_required"]),
                inherited_fd_policy=str(command["fd_policy"]),
                inherited_fd_count=require_int(
                    command["inherited_fd_count"], "command inherited fd count"
                ),
                executable_identity=require_str_mapping(
                    payload["executable_identity"], "executable identity"
                ),
                plugin_identity=require_str_mapping(payload["plugin_identity"], "plugin identity"),
                branch_identity=require_str_mapping(payload["branch_identity"], "branch identity"),
                worktree_identity=require_str_mapping(
                    payload["worktree_identity"], "worktree identity"
                ),
                projection_identity=require_str_mapping(
                    payload["projection_identity"], "projection identity"
                ),
                artifact_paths=tuple(
                    str(item)
                    for item in require_sequence(payload["artifact_paths"], "artifact paths")
                ),
                nonsecret_env=nonsecret_env,
                secret_bindings=secret_bindings,
                quota_identity=require_str_mapping(payload["quota_identity"], "quota identity"),
            )
        except LaunchContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LaunchContractError("invalid persisted launch contract") from exc

        supplied_json = json.dumps(
            _json_value(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if supplied_json != contract.canonical_json:
            raise LaunchContractError("persisted launch contract is not canonical")
        if expected_digest is not None and contract.digest != expected_digest:
            raise LaunchContractError("persisted launch contract digest mismatch")
        return contract
