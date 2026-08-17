"""Non-executable skill projection evidence bound beneath a physical launch."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from types import MappingProxyType

from ._type_backend import CmdSpec

__all__ = ["LaunchContractError", "SkillProjectionBinding"]


class LaunchContractError(ValueError):
    """A launch could not be proven to match its declared authority."""


def _freeze_str_mapping(value: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    frozen: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise LaunchContractError(f"{field_name} must map strings to strings")
        frozen[key] = item
    return MappingProxyType(dict(sorted(frozen.items())))


def _freeze_metadata(value: Mapping[str, object]) -> Mapping[str, object]:
    """Freeze untrusted hints even though they have no backend authority."""

    def freeze(item: object) -> object:
        if isinstance(item, Mapping):
            return MappingProxyType(
                {str(key): freeze(nested) for key, nested in sorted(item.items())}
            )
        if isinstance(item, (list, tuple)):
            return tuple(freeze(nested) for nested in item)
        if isinstance(item, set):
            return frozenset(freeze(nested) for nested in item)
        return item

    return MappingProxyType({key: freeze(item) for key, item in sorted(value.items())})


def _payload_value(value: object) -> object:
    """Return an immutable JSON-shaped value."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _payload_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_payload_value(item) for item in value)
    return value


def _json_value(value: object) -> object:
    """Thaw an immutable payload into stdlib JSON containers."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SkillProjectionBinding:
    """Backend-adapted, non-executable skill projection bound beneath one launch."""

    root_name: str | None
    member_names: tuple[str, ...]
    execution_role: str
    capability_union: frozenset[str]
    source_identities: Mapping[str, Mapping[str, object]]
    canonical_digests: Mapping[str, str]
    projected_digests: Mapping[str, str]
    semantic_digests: Mapping[str, str]
    adaptation_digests: Mapping[str, str]
    projection_version: int
    project_root: str | None
    cwd: str
    backend: str
    artifact_paths: tuple[str, ...] = ()
    command_digest: str = ""
    branch_identity: Mapping[str, str] = field(default_factory=dict)
    worktree_identity: Mapping[str, str] = field(default_factory=dict)
    executable_identity: Mapping[str, str] = field(default_factory=dict)
    plugin_identity: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "member_names", tuple(self.member_names))
        object.__setattr__(self, "capability_union", frozenset(self.capability_union))
        object.__setattr__(self, "artifact_paths", tuple(self.artifact_paths))
        for field_name in (
            "branch_identity",
            "worktree_identity",
            "executable_identity",
            "plugin_identity",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_str_mapping(getattr(self, field_name), field_name.replace("_", " ")),
            )
        if self.command_digest and (
            len(self.command_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.command_digest)
        ):
            raise LaunchContractError("skill projection command digest must be sha256")
        if not self.member_names or len(self.member_names) != len(set(self.member_names)):
            raise LaunchContractError("skill projection binding requires unique members")
        if not self.execution_role or not self.cwd or not self.backend:
            raise LaunchContractError("skill projection binding requires role, cwd, and backend")
        if self.projection_version < 1:
            raise LaunchContractError("skill projection binding version must be positive")
        expected = set(self.member_names)
        frozen_sources: dict[str, Mapping[str, object]] = {}
        for name, identity in self.source_identities.items():
            if not isinstance(name, str) or not isinstance(identity, Mapping):
                raise LaunchContractError("skill projection source identities are malformed")
            frozen_sources[name] = _freeze_metadata(identity)
        object.__setattr__(
            self,
            "source_identities",
            MappingProxyType(dict(sorted(frozen_sources.items()))),
        )
        for field_name in (
            "canonical_digests",
            "projected_digests",
            "semantic_digests",
            "adaptation_digests",
        ):
            mapping = _freeze_str_mapping(getattr(self, field_name), field_name.replace("_", " "))
            if set(mapping) != expected:
                raise LaunchContractError(
                    f"skill projection {field_name.replace('_', ' ')} do not match members"
                )
            object.__setattr__(self, field_name, mapping)
        if set(self.source_identities) != expected:
            raise LaunchContractError("skill projection source identities do not match members")
        for field_name in ("canonical_digests", "projected_digests"):
            for digest in getattr(self, field_name).values():
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    raise LaunchContractError(
                        f"skill projection {field_name.replace('_', ' ')} must be sha256"
                    )
        for name in self.member_names:
            semantic = self.semantic_digests[name]
            adaptation = self.adaptation_digests[name]
            if bool(semantic) != bool(adaptation):
                raise LaunchContractError(
                    f"skill projection semantic/adaptation observation is incomplete for {name!r}"
                )
            for digest in (semantic, adaptation):
                if digest and (
                    len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
                ):
                    raise LaunchContractError("skill projection semantic digest must be sha256")

    @property
    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "root_name": self.root_name,
                "member_names": self.member_names,
                "execution_role": self.execution_role,
                "capability_union": tuple(sorted(self.capability_union)),
                "source_identities": self.source_identities,
                "canonical_digests": self.canonical_digests,
                "projected_digests": self.projected_digests,
                "semantic_digests": self.semantic_digests,
                "adaptation_digests": self.adaptation_digests,
                "projection_version": self.projection_version,
                "project_root": self.project_root,
                "cwd": self.cwd,
                "backend": self.backend,
                "artifact_paths": self.artifact_paths,
                "command_digest": self.command_digest,
                "branch_identity": self.branch_identity,
                "worktree_identity": self.worktree_identity,
                "executable_identity": self.executable_identity,
                "plugin_identity": self.plugin_identity,
            }
        )

    @property
    def projection_digest(self) -> str:
        payload = dict(self.canonical_payload)
        for field_name in (
            "artifact_paths",
            "command_digest",
            "branch_identity",
            "worktree_identity",
            "executable_identity",
            "plugin_identity",
        ):
            payload.pop(field_name)
        canonical_json = json.dumps(
            _json_value(_payload_value(payload)),
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical_json.encode()).hexdigest()

    @property
    def digest(self) -> str:
        canonical_json = json.dumps(
            _json_value(_payload_value(self.canonical_payload)),
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical_json.encode()).hexdigest()

    @property
    def finalized(self) -> bool:
        return bool(self.command_digest)

    def bind_launch(
        self,
        *,
        cmd_spec: CmdSpec,
        branch_identity: Mapping[str, str],
        worktree_identity: Mapping[str, str],
        executable_identity: Mapping[str, str],
        plugin_identity: Mapping[str, str],
        artifact_paths: tuple[str, ...],
    ) -> SkillProjectionBinding:
        """Derive exact physical launch evidence without granting executable authority."""
        if cmd_spec.cwd != self.cwd:
            raise LaunchContractError("skill projection cwd drifted from exact command")
        origin = cmd_spec.origin
        payload = {
            "argv": cmd_spec.cmd,
            "cwd": cmd_spec.cwd,
            "origin": (
                {
                    "binary": origin.binary,
                    "mode_flags": origin.mode_flags,
                    "kv_flags": origin.kv_flags,
                    "positional": origin.positional,
                    "variadic_pairs": origin.variadic_pairs,
                }
                if origin is not None
                else None
            ),
            "nonsecret_env": cmd_spec.env,
            "process_idle_timeout_ms": cmd_spec.process_idle_timeout_ms,
        }
        command_digest = sha256(
            json.dumps(
                _json_value(_payload_value(payload)),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return replace(
            self,
            command_digest=command_digest,
            branch_identity=branch_identity,
            worktree_identity=worktree_identity,
            executable_identity=executable_identity,
            plugin_identity=plugin_identity,
            artifact_paths=artifact_paths,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> SkillProjectionBinding:
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

        try:
            members_raw = payload["member_names"]
            capabilities_raw = payload["capability_union"]
            artifacts_raw = payload["artifact_paths"]
            if not isinstance(members_raw, (list, tuple)):
                raise LaunchContractError("skill projection members must be an array")
            if not isinstance(capabilities_raw, (list, tuple)):
                raise LaunchContractError("skill projection capabilities must be an array")
            if not isinstance(artifacts_raw, (list, tuple)):
                raise LaunchContractError("skill projection artifact paths must be an array")
            sources = require_mapping(payload["source_identities"], "source identities")
            projection_version = payload["projection_version"]
            if not isinstance(projection_version, int) or isinstance(projection_version, bool):
                raise LaunchContractError("skill projection version must be an integer")
            return cls(
                root_name=(
                    str(payload["root_name"]) if payload["root_name"] is not None else None
                ),
                member_names=tuple(str(item) for item in members_raw),
                execution_role=str(payload["execution_role"]),
                capability_union=frozenset(str(item) for item in capabilities_raw),
                source_identities={
                    str(key): require_mapping(value, f"source identity {key}")
                    for key, value in sources.items()
                },
                canonical_digests=require_str_mapping(
                    payload["canonical_digests"], "canonical digests"
                ),
                projected_digests=require_str_mapping(
                    payload["projected_digests"], "projected digests"
                ),
                semantic_digests=require_str_mapping(
                    payload["semantic_digests"], "semantic digests"
                ),
                adaptation_digests=require_str_mapping(
                    payload["adaptation_digests"], "adaptation digests"
                ),
                projection_version=projection_version,
                project_root=(
                    str(payload["project_root"]) if payload["project_root"] is not None else None
                ),
                cwd=str(payload["cwd"]),
                backend=str(payload["backend"]),
                artifact_paths=tuple(str(item) for item in artifacts_raw),
                command_digest=str(payload["command_digest"]),
                branch_identity=require_str_mapping(payload["branch_identity"], "branch identity"),
                worktree_identity=require_str_mapping(
                    payload["worktree_identity"], "worktree identity"
                ),
                executable_identity=require_str_mapping(
                    payload["executable_identity"], "executable identity"
                ),
                plugin_identity=require_str_mapping(payload["plugin_identity"], "plugin identity"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, LaunchContractError):
                raise
            raise LaunchContractError("skill projection binding payload is malformed") from exc
