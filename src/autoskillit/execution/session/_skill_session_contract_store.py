"""Persistent effective-skill contracts for resumable backend sessions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import RLock
from types import MappingProxyType
from typing import Any

import regex as re

from autoskillit.core import (
    SKILL_PROJECTION_VERSION,
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
    SkillSourceRef,
    WriteBehaviorSpec,
    atomic_write,
    default_log_dir,
    derive_backend_requirements,
    read_versioned_json,
    validate_skill_capability_roles,
    write_versioned_json,
)

__all__ = [
    "DefaultSkillSessionContractStore",
    "SkillSessionContract",
    "StoredSkillSessionContract",
    "delete_skill_session_contracts",
]

SKILL_SESSION_CONTRACT_SCHEMA_VERSION = 2
_STORE_MANIFEST_SCHEMA_VERSION = 1
_MANIFEST_FILENAME = "manifest.json"
_SNAPSHOT_DIRNAME = "snapshot"
_CORRELATION_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SkillSessionContract:
    """Immutable execution contract bound to a projected skill snapshot."""

    root_name: str
    execution_role: SkillExecutionRole
    source_refs: Mapping[str, SkillSourceRef]
    closure: tuple[str, ...]
    capability_union: frozenset[str]
    canonical_digests: Mapping[str, str]
    projected_digests: Mapping[str, str]
    projection_version: int
    project_root: str
    cwd: str
    backend: str
    resolved_command: str
    member_roles: Mapping[str, SkillExecutionRole]
    member_capabilities: Mapping[str, frozenset[str]]
    member_activate_deps: Mapping[str, tuple[str, ...]]
    canonical_contents: Mapping[str, str]
    expected_output_patterns: tuple[str, ...] = ()
    write_behavior: WriteBehaviorSpec = WriteBehaviorSpec()
    read_only: bool = False
    completion_required: bool = False
    skill_contract_json: str = ""
    projection_substitutions: tuple[tuple[str, str], ...] = ()
    projection_gating: bool | None = None
    projection_namespace: str | None = None
    schema_version: int = SKILL_SESSION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_refs", MappingProxyType(dict(self.source_refs)))
        object.__setattr__(
            self,
            "canonical_digests",
            MappingProxyType(dict(self.canonical_digests)),
        )
        object.__setattr__(
            self,
            "projected_digests",
            MappingProxyType(dict(self.projected_digests)),
        )
        object.__setattr__(self, "member_roles", MappingProxyType(dict(self.member_roles)))
        object.__setattr__(
            self,
            "member_capabilities",
            MappingProxyType(
                {
                    name: frozenset(capabilities)
                    for name, capabilities in self.member_capabilities.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "member_activate_deps",
            MappingProxyType(
                {
                    name: tuple(dependencies)
                    for name, dependencies in self.member_activate_deps.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "canonical_contents",
            MappingProxyType(dict(self.canonical_contents)),
        )

    @property
    def backend_requirements(self) -> frozenset[str]:
        """Derive backend constraints from the persisted capability union."""
        validate_skill_capability_roles(self.capability_union, self.execution_role)
        return derive_backend_requirements(self.capability_union)


@dataclass(frozen=True, slots=True)
class StoredSkillSessionContract:
    """Validated contract plus the retained projected snapshot directory."""

    contract: SkillSessionContract
    snapshot_dir: Path
    raw_session_id: str


class DefaultSkillSessionContractStore:
    """Filesystem-backed provisional-to-final session contract store."""

    def __init__(self, root: Path | None = None) -> None:
        configured_root = root or (default_log_dir() / "skill-session-contracts")
        self._root = configured_root.expanduser().resolve()
        self._provisional_root = self._root / "provisional"
        self._sessions_root = self._root / "sessions"
        self._provisional_root.mkdir(parents=True, exist_ok=True)
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def create_provisional(
        self,
        contract: SkillSessionContract,
        snapshot: Mapping[str, str],
    ) -> str:
        """Atomically persist an unbound contract under a random correlation key."""
        _validate_contract(contract)
        snapshot_paths = _validate_snapshot_mapping(contract, snapshot)
        correlation_key = secrets.token_hex(16)
        destination = self._provisional_path(correlation_key)
        temp_path = Path(
            tempfile.mkdtemp(prefix=".create-", dir=str(self._provisional_root))
        ).resolve()
        self._ensure_contained(temp_path)
        try:
            snapshot_root = temp_path / _SNAPSHOT_DIRNAME
            for relative_path, content in snapshot.items():
                target = snapshot_root / PurePosixPath(relative_path)
                self._ensure_contained(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, content)
            manifest = _build_manifest(
                contract=contract,
                raw_session_id=None,
                candidate_session_ids=(),
                snapshot_paths=snapshot_paths,
            )
            write_versioned_json(
                temp_path / _MANIFEST_FILENAME,
                manifest,
                schema_version=_STORE_MANIFEST_SCHEMA_VERSION,
            )
            with self._lock:
                os.replace(temp_path, destination)
        except Exception:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise
        return correlation_key

    def observe_candidate(self, correlation_key: str, session_id: str) -> None:
        """Record a repeated advisory candidate without binding store ownership."""
        _validate_raw_session_id(session_id)
        provisional = self._provisional_path(correlation_key)
        with self._lock:
            manifest = self._read_manifest(provisional)
            candidates = list(manifest.get("candidate_session_ids", []))
            if session_id not in candidates:
                candidates.append(session_id)
                manifest["candidate_session_ids"] = candidates
                self._write_manifest(provisional, manifest)

    def finalize(self, correlation_key: str, session_id: str) -> None:
        """Bind a provisional entry only to the final backend session ID."""
        _validate_raw_session_id(session_id)
        provisional = self._provisional_path(correlation_key)
        destination = self._session_path(session_id)
        with self._lock:
            manifest = self._read_manifest(provisional)
            self._validate_entry(provisional, manifest, expected_raw_session_id=None)
            manifest["raw_session_id"] = session_id
            self._write_manifest(provisional, manifest)
            if destination.exists():
                existing = self._read_manifest(destination)
                self._validate_entry(
                    destination,
                    existing,
                    expected_raw_session_id=session_id,
                )
                raise FileExistsError(f"Session contract already finalized: {session_id!r}")
            os.replace(provisional, destination)

    def load(self, session_id: str) -> StoredSkillSessionContract:
        """Load and fully validate the contract bound to ``session_id``."""
        _validate_raw_session_id(session_id)
        entry = self._session_path(session_id)
        with self._lock:
            manifest = self._read_manifest(entry)
            contract = self._validate_entry(
                entry,
                manifest,
                expected_raw_session_id=session_id,
            )
        return StoredSkillSessionContract(
            contract=contract,
            snapshot_dir=entry / _SNAPSHOT_DIRNAME,
            raw_session_id=session_id,
        )

    def delete(self, session_id: str) -> None:
        """Explicitly delete finalized retained state for one raw session ID."""
        with self._lock:
            _delete_finalized_contract(self._sessions_root, session_id)

    def discard(self, correlation_key: str) -> None:
        """Explicitly discard a provisional entry that never finalized."""
        entry = self._provisional_path(correlation_key)
        with self._lock:
            shutil.rmtree(entry, ignore_errors=True)

    def _provisional_path(self, correlation_key: str) -> Path:
        if not _CORRELATION_KEY_RE.fullmatch(correlation_key):
            raise ValueError(f"Invalid correlation key: {correlation_key!r}")
        path = self._provisional_root / correlation_key
        self._ensure_contained(path)
        return path

    def _session_path(self, session_id: str) -> Path:
        path = _finalized_contract_path(self._sessions_root, session_id)
        self._ensure_contained(path)
        return path

    def _ensure_contained(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self._root):
            raise ValueError(f"Skill session contract path escapes store root: {path}")

    def _read_manifest(self, entry: Path) -> dict[str, Any]:
        self._ensure_contained(entry)
        manifest_path = entry / _MANIFEST_FILENAME
        if not manifest_path.exists():
            raise FileNotFoundError(f"Skill session contract not found: {entry.name}") from None
        loaded = read_versioned_json(manifest_path, _STORE_MANIFEST_SCHEMA_VERSION)
        if loaded is None:
            raise ValueError("Invalid or unsupported skill session contract manifest")
        return loaded

    def _write_manifest(self, entry: Path, manifest: Mapping[str, Any]) -> None:
        self._ensure_contained(entry)
        write_versioned_json(
            entry / _MANIFEST_FILENAME,
            dict(manifest),
            schema_version=_STORE_MANIFEST_SCHEMA_VERSION,
        )

    def _validate_entry(
        self,
        entry: Path,
        manifest: Mapping[str, Any],
        *,
        expected_raw_session_id: str | None,
    ) -> SkillSessionContract:
        if manifest.get("schema_version") != _STORE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("Unsupported skill session store schema")
        raw_session_id = manifest.get("raw_session_id")
        if raw_session_id != expected_raw_session_id:
            raise ValueError("Skill session contract raw session ID mismatch")

        contract_data = manifest.get("contract")
        if not isinstance(contract_data, dict):
            raise ValueError("Skill session contract manifest is missing contract")
        expected_contract_digest = _digest_json(contract_data)
        if manifest.get("contract_digest") != expected_contract_digest:
            raise ValueError("Skill session contract manifest digest mismatch")
        contract = _contract_from_dict(contract_data)
        _validate_contract(contract)

        snapshot_paths_raw = manifest.get("snapshot_paths")
        if not isinstance(snapshot_paths_raw, dict):
            raise ValueError("Skill session contract manifest is missing snapshot paths")
        snapshot_paths = {
            str(name): str(relative_path) for name, relative_path in snapshot_paths_raw.items()
        }
        if set(snapshot_paths) != set(contract.closure):
            raise ValueError("Skill session contract snapshot closure mismatch")

        snapshot_root = entry / _SNAPSHOT_DIRNAME
        self._ensure_contained(snapshot_root)
        declared_files: set[Path] = set()
        for name, relative_path in snapshot_paths.items():
            safe_relative = _validate_relative_path(relative_path)
            projected_path = snapshot_root / safe_relative
            self._ensure_contained(projected_path)
            declared_files.add(safe_relative)
            try:
                content = projected_path.read_bytes()
            except OSError as exc:
                raise ValueError(
                    f"Skill session projected snapshot is unreadable for {name!r}"
                ) from exc
            digest = hashlib.sha256(content).hexdigest()
            if digest != contract.projected_digests[name]:
                raise ValueError(f"Skill session projected digest mismatch for {name!r}")

        actual_files = (
            {
                path.relative_to(snapshot_root)
                for path in snapshot_root.rglob("*")
                if path.is_file()
            }
            if snapshot_root.is_dir()
            else set()
        )
        if actual_files != declared_files:
            raise ValueError("Skill session projected snapshot file set mismatch")
        return contract


def delete_skill_session_contracts(
    session_ids: Iterable[str],
    *,
    root: Path | None = None,
) -> None:
    """Explicitly remove retained contracts for completed external sessions."""
    configured_root = root or (default_log_dir() / "skill-session-contracts")
    sessions_root = configured_root.expanduser().resolve() / "sessions"
    for session_id in session_ids:
        if session_id:
            _delete_finalized_contract(sessions_root, session_id)


def _validate_raw_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id or "\x00" in session_id:
        raise ValueError(f"Invalid session ID: {session_id!r}")


def _finalized_contract_path(sessions_root: Path, session_id: str) -> Path:
    _validate_raw_session_id(session_id)
    key = hashlib.sha256(session_id.encode()).hexdigest()
    path = sessions_root / key
    if not path.resolve().is_relative_to(sessions_root.resolve()):
        raise ValueError(f"Skill session contract path escapes sessions root: {path}")
    return path


def _delete_finalized_contract(sessions_root: Path, session_id: str) -> None:
    shutil.rmtree(
        _finalized_contract_path(sessions_root, session_id),
        ignore_errors=True,
    )


def _validate_digest_map(
    field_name: str,
    digests: Mapping[str, str],
    closure: set[str],
) -> None:
    if set(digests) != closure:
        raise SkillContractError(f"{field_name} keys must exactly match closure")
    for name, digest in digests.items():
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise SkillContractError(f"Invalid {field_name} digest for {name!r}")


def _validate_contract(contract: SkillSessionContract) -> None:
    if contract.schema_version != SKILL_SESSION_CONTRACT_SCHEMA_VERSION:
        raise SkillContractError("Unsupported skill session contract schema")
    if not contract.root_name or not contract.closure:
        raise SkillContractError("Skill session contract requires a root and closure")
    closure = set(contract.closure)
    if len(closure) != len(contract.closure) or contract.root_name not in closure:
        raise SkillContractError("Skill session contract closure is invalid")
    if set(contract.source_refs) != closure:
        raise SkillContractError("source_refs keys must exactly match closure")
    if set(contract.member_roles) != closure:
        raise SkillContractError("member_roles keys must exactly match closure")
    if set(contract.member_capabilities) != closure:
        raise SkillContractError("member_capabilities keys must exactly match closure")
    if set(contract.member_activate_deps) != closure:
        raise SkillContractError("member_activate_deps keys must exactly match closure")
    if set(contract.canonical_contents) != closure:
        raise SkillContractError("canonical_contents keys must exactly match closure")
    for name in contract.closure:
        source_ref = contract.source_refs[name]
        if not isinstance(source_ref, SkillSourceRef):
            raise SkillContractError(f"source reference for {name!r} must be typed")
        if source_ref.logical_name != name:
            raise SkillContractError(f"source reference logical name mismatch for {name!r}")
        role = contract.member_roles[name]
        capabilities = contract.member_capabilities[name]
        validate_skill_capability_roles(capabilities, role)
        if role is not contract.execution_role:
            raise SkillContractError(f"member role mismatch for {name!r}")
        canonical_digest = hashlib.sha256(contract.canonical_contents[name].encode()).hexdigest()
        if canonical_digest != contract.canonical_digests[name]:
            raise SkillContractError(f"canonical content digest mismatch for {name!r}")
    member_capability_union = frozenset().union(
        *(contract.member_capabilities[name] for name in contract.closure)
    )
    if member_capability_union != contract.capability_union:
        raise SkillContractError("member capability union does not match contract")
    _validate_digest_map("canonical_digests", contract.canonical_digests, closure)
    _validate_digest_map("projected_digests", contract.projected_digests, closure)
    if contract.projection_version != SKILL_PROJECTION_VERSION:
        raise SkillContractError(
            f"unsupported projection_version {contract.projection_version}; "
            f"expected {SKILL_PROJECTION_VERSION}"
        )
    if not contract.project_root or not contract.cwd:
        raise SkillContractError("project_root and cwd are required")
    if not contract.backend or not contract.resolved_command:
        raise SkillContractError("backend and resolved_command are required")
    validate_skill_capability_roles(contract.capability_union, contract.execution_role)


def _validate_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Projected snapshot path must be a non-empty relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError(f"Unsafe projected snapshot path: {value!r}")
    return Path(*relative.parts)


def _validate_snapshot_mapping(
    contract: SkillSessionContract,
    snapshot: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("Projected snapshot must be a mapping")
    by_skill: dict[str, str] = {}
    seen_paths: set[Path] = set()
    for raw_relative_path, content in snapshot.items():
        relative_path = _validate_relative_path(raw_relative_path)
        if relative_path in seen_paths:
            raise ValueError(f"Duplicate projected snapshot path: {raw_relative_path!r}")
        if relative_path.name != "SKILL.md" or len(relative_path.parts) < 2:
            raise ValueError(
                f"Projected snapshot path must end in <skill>/SKILL.md: {raw_relative_path!r}"
            )
        if not isinstance(content, str):
            raise ValueError(f"Projected snapshot content must be text: {raw_relative_path!r}")
        skill_name = relative_path.parent.name
        if skill_name in by_skill:
            raise ValueError(f"Multiple projected documents for skill {skill_name!r}")
        digest = hashlib.sha256(content.encode()).hexdigest()
        if contract.projected_digests.get(skill_name) != digest:
            raise ValueError(f"Projected snapshot digest mismatch for {skill_name!r}")
        by_skill[skill_name] = relative_path.as_posix()
        seen_paths.add(relative_path)
    if set(by_skill) != set(contract.closure):
        raise ValueError("Projected snapshot documents must exactly match closure")
    return by_skill


def _source_ref_to_dict(source_ref: SkillSourceRef) -> dict[str, Any]:
    return {
        "origin": source_ref.origin.value,
        "logical_name": source_ref.logical_name,
        "skill_path": str(source_ref.skill_path),
        "search_dir": source_ref.search_dir,
        "precedence": source_ref.precedence,
    }


def _source_ref_from_dict(data: Mapping[str, Any]) -> SkillSourceRef:
    try:
        return SkillSourceRef(
            origin=SkillSource(str(data["origin"])),
            logical_name=str(data["logical_name"]),
            skill_path=Path(str(data["skill_path"])),
            search_dir=(str(data["search_dir"]) if data.get("search_dir") is not None else None),
            precedence=(int(data["precedence"]) if data.get("precedence") is not None else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid skill source reference") from exc


def _contract_to_dict(contract: SkillSessionContract) -> dict[str, Any]:
    return {
        "schema_version": contract.schema_version,
        "root_name": contract.root_name,
        "execution_role": contract.execution_role.value,
        "source_refs": {
            name: _source_ref_to_dict(source_ref)
            for name, source_ref in sorted(contract.source_refs.items())
        },
        "closure": list(contract.closure),
        "capability_union": sorted(contract.capability_union),
        "canonical_digests": dict(sorted(contract.canonical_digests.items())),
        "projected_digests": dict(sorted(contract.projected_digests.items())),
        "projection_version": contract.projection_version,
        "project_root": contract.project_root,
        "cwd": contract.cwd,
        "backend": contract.backend,
        "resolved_command": contract.resolved_command,
        "member_roles": {name: role.value for name, role in sorted(contract.member_roles.items())},
        "member_capabilities": {
            name: sorted(capabilities)
            for name, capabilities in sorted(contract.member_capabilities.items())
        },
        "member_activate_deps": {
            name: list(dependencies)
            for name, dependencies in sorted(contract.member_activate_deps.items())
        },
        "canonical_contents": dict(sorted(contract.canonical_contents.items())),
        "expected_output_patterns": list(contract.expected_output_patterns),
        "write_behavior": {
            "mode": contract.write_behavior.mode,
            "expected_when": list(contract.write_behavior.expected_when),
        },
        "read_only": contract.read_only,
        "completion_required": contract.completion_required,
        "skill_contract_json": contract.skill_contract_json,
        "projection_substitutions": [list(item) for item in contract.projection_substitutions],
        "projection_gating": contract.projection_gating,
        "projection_namespace": contract.projection_namespace,
    }


def _contract_from_dict(data: Mapping[str, Any]) -> SkillSessionContract:
    try:
        source_refs_raw = data["source_refs"]
        if not isinstance(source_refs_raw, dict):
            raise ValueError("source_refs must be an object")
        return SkillSessionContract(
            root_name=str(data["root_name"]),
            execution_role=SkillExecutionRole(str(data["execution_role"])),
            source_refs={
                str(name): _source_ref_from_dict(source_ref)
                for name, source_ref in source_refs_raw.items()
                if isinstance(source_ref, dict)
            },
            closure=tuple(str(name) for name in data["closure"]),
            capability_union=frozenset(str(cap) for cap in data["capability_union"]),
            canonical_digests={
                str(name): str(digest) for name, digest in data["canonical_digests"].items()
            },
            projected_digests={
                str(name): str(digest) for name, digest in data["projected_digests"].items()
            },
            projection_version=int(data["projection_version"]),
            project_root=str(data["project_root"]),
            cwd=str(data["cwd"]),
            backend=str(data["backend"]),
            resolved_command=str(data["resolved_command"]),
            member_roles={
                str(name): SkillExecutionRole(str(role))
                for name, role in data["member_roles"].items()
            },
            member_capabilities={
                str(name): frozenset(str(capability) for capability in capabilities)
                for name, capabilities in data["member_capabilities"].items()
            },
            member_activate_deps={
                str(name): tuple(str(dependency) for dependency in dependencies)
                for name, dependencies in data["member_activate_deps"].items()
            },
            canonical_contents={
                str(name): str(content) for name, content in data["canonical_contents"].items()
            },
            expected_output_patterns=tuple(
                str(pattern) for pattern in data.get("expected_output_patterns", [])
            ),
            write_behavior=WriteBehaviorSpec(
                mode=(
                    str(data.get("write_behavior", {}).get("mode"))
                    if data.get("write_behavior", {}).get("mode") is not None
                    else None
                ),
                expected_when=tuple(
                    str(pattern)
                    for pattern in data.get("write_behavior", {}).get("expected_when", [])
                ),
            ),
            read_only=bool(data.get("read_only", False)),
            completion_required=bool(data.get("completion_required", False)),
            skill_contract_json=str(data.get("skill_contract_json", "")),
            projection_substitutions=tuple(
                (str(item[0]), str(item[1])) for item in data.get("projection_substitutions", [])
            ),
            projection_gating=data.get("projection_gating"),
            projection_namespace=(
                str(data["projection_namespace"])
                if data.get("projection_namespace") is not None
                else None
            ),
            schema_version=int(data["schema_version"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Invalid serialized skill session contract") from exc


def _digest_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _build_manifest(
    *,
    contract: SkillSessionContract,
    raw_session_id: str | None,
    candidate_session_ids: tuple[str, ...],
    snapshot_paths: Mapping[str, str],
) -> dict[str, Any]:
    contract_data = _contract_to_dict(contract)
    return {
        "schema_version": _STORE_MANIFEST_SCHEMA_VERSION,
        "raw_session_id": raw_session_id,
        "candidate_session_ids": list(candidate_session_ids),
        "contract": contract_data,
        "contract_digest": _digest_json(contract_data),
        "snapshot_paths": dict(sorted(snapshot_paths.items())),
    }
