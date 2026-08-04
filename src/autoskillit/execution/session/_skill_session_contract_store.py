"""Persistent effective-skill contracts for resumable backend sessions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any

import regex as re

from autoskillit.core import (
    SKILL_PROJECTION_VERSION,
    SKILL_SESSION_CONTRACT_SCHEMA_VERSION,
    ChildExecutionIdentity,
    ExecutionIdentity,
    ExplorationTaskSpec,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    ManagedHeadlessSessionLineageRef,
    ResolvedLaunchContract,
    RelationshipKind,
    RepositoryProfileId,
    SkillContractError,
    SkillExecutionRole,
    SkillSessionContract,
    SkillSource,
    SkillSourceRef,
    StoredSkillSessionContract,
    WriteBehaviorSpec,
    atomic_write,
    default_log_dir,
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

_STORE_MANIFEST_SCHEMA_VERSION = 2
_MANIFEST_FILENAME = "manifest.json"
_SNAPSHOT_DIRNAME = "snapshot"
_CORRELATION_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
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
                managed_lineage_ref=managed_lineage_ref,
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

    def bind_launch(
        self,
        correlation_key: str,
        launch_contract: ResolvedLaunchContract,
    ) -> None:
        """Bind the exact secret-free physical authority before the child spawn."""
        provisional = self._provisional_path(correlation_key)
        with self._lock:
            manifest = self._read_manifest(provisional)
            contract = self._validate_entry(
                provisional,
                manifest,
                expected_raw_session_id=None,
            )
            if contract.launch_contract is not None:
                previous = contract.launch_contract
                if (
                    previous.surface is not launch_contract.surface
                    or previous.effective_backend != launch_contract.effective_backend
                    or previous.backend_authority != launch_contract.backend_authority
                    or previous.semantic_digest != launch_contract.semantic_digest
                    or previous.projection_digest != launch_contract.projection_digest
                ):
                    raise ValueError("Skill session launch authority drifted between attempts")
                if previous.digest == launch_contract.digest:
                    return
            bound_contract = replace(
                contract,
                launch_contract=launch_contract,
                launch_contract_digest=launch_contract.digest,
            )
            _validate_contract(bound_contract)
            contract_data = _contract_to_dict(bound_contract)
            manifest["contract"] = contract_data
            manifest["contract_digest"] = _digest_json(contract_data)
            self._write_manifest(provisional, manifest)

    def rebind_final_session(
        self,
        session_id: str,
        final_session_id: str,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef,
    ) -> None:
        """Move finalized ownership after a verified same-lineage continuation."""
        _validate_raw_session_id(session_id)
        _validate_raw_session_id(final_session_id)
        if not isinstance(managed_lineage_ref, ManagedHeadlessSessionLineageRef):
            raise TypeError("managed_lineage_ref must be a ManagedHeadlessSessionLineageRef")
        source = self._session_path(session_id)
        destination = self._session_path(final_session_id)
        with self._lock:
            manifest = self._read_manifest(source)
            self._validate_entry(
                source,
                manifest,
                expected_raw_session_id=session_id,
            )
            stored_ref = _managed_lineage_ref_from_manifest(manifest)
            if stored_ref is None or stored_ref != managed_lineage_ref:
                raise ValueError("Skill session managed lineage reference mismatch")
            if session_id == final_session_id:
                return
            if destination.exists():
                existing = self._read_manifest(destination)
                self._validate_entry(
                    destination,
                    existing,
                    expected_raw_session_id=final_session_id,
                )
                raise FileExistsError(f"Session contract already finalized: {final_session_id!r}")
            manifest["raw_session_id"] = final_session_id
            self._write_manifest(source, manifest)
            os.replace(source, destination)

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
            managed_lineage_ref=_managed_lineage_ref_from_manifest(manifest),
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
        _managed_lineage_ref_from_manifest(manifest)
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
    if contract.launch_contract is not None:
        if contract.launch_contract_digest != contract.launch_contract.digest:
            raise SkillContractError("Skill session launch contract digest mismatch")
        if contract.launch_contract.effective_backend != contract.backend:
            raise SkillContractError("Skill session launch backend mismatch")
        if contract.launch_contract.cwd != contract.cwd:
            raise SkillContractError("Skill session launch cwd mismatch")
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
    if set(contract.exploration_vectors) != closure:
        raise SkillContractError("exploration_vectors keys must exactly match closure")
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
        vectors = contract.exploration_vectors[name]
        if any(not isinstance(vector, ExplorationVectorDef) for vector in vectors):
            raise SkillContractError(f"exploration vectors for {name!r} must be typed")
        vector_ids = tuple(vector.id for vector in vectors)
        if len(vector_ids) != len(set(vector_ids)):
            raise SkillContractError(f"exploration vector ids for {name!r} must be unique")
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


_SERIALIZED_EXPLORATION_VECTOR_KEYS = frozenset(
    {
        "id",
        "disposition",
        "rationale",
        "applicability",
        "role",
        "profile",
        "relationship_classes",
        "task_id",
        "frontier_item_id",
        "depends_on",
        "scope",
        "max_results",
        "max_report_bytes",
        "evidence_version",
        "native_dispatch",
        "body",
        "digest",
    }
)
_EXECUTION_IDENTITY_KEYS = frozenset(ExecutionIdentity.empty().to_dict())
_CHILD_EXECUTION_IDENTITY_KEYS = frozenset(
    {
        "task_id",
        "role",
        "plan_digest",
        "definition_digest",
        "requested_backend",
        "effective_backend",
        "requested_model",
        "effective_model",
        "requested_effort",
        "effective_effort",
        "session_id",
    }
)


def _exploration_vector_to_dict(vector: ExplorationVectorDef) -> dict[str, Any]:
    task = vector.task
    return {
        "id": vector.id,
        "disposition": vector.disposition.value,
        "rationale": vector.rationale,
        "applicability": vector.applicability.value,
        "role": vector.role,
        "profile": vector.profile.value,
        "relationship_classes": [item.value for item in vector.relationship_classes],
        "task_id": task.task_id,
        "frontier_item_id": task.frontier_item_id,
        "depends_on": list(task.depends_on),
        "scope": list(task.scope),
        "max_results": vector.max_results,
        "max_report_bytes": vector.max_report_bytes,
        "evidence_version": vector.evidence_version,
        "native_dispatch": vector.native_dispatch,
        "body": vector.body,
        "digest": vector.digest,
    }


def _exploration_vector_from_dict(value: object) -> ExplorationVectorDef:
    if not isinstance(value, dict) or set(value) != _SERIALIZED_EXPLORATION_VECTOR_KEYS:
        raise ValueError("serialized exploration vector keys are invalid")
    for field_name in ("relationship_classes", "depends_on", "scope"):
        field_value = value[field_name]
        if not isinstance(field_value, list) or any(
            not isinstance(item, str) for item in field_value
        ):
            raise ValueError(f"serialized exploration vector {field_name} is invalid")
    for field_name in (
        "id",
        "disposition",
        "rationale",
        "applicability",
        "profile",
        "task_id",
        "frontier_item_id",
        "body",
        "digest",
    ):
        if not isinstance(value[field_name], str):
            raise ValueError(f"serialized exploration vector {field_name} is invalid")
    if value["role"] is not None and not isinstance(value["role"], str):
        raise ValueError("serialized exploration vector role is invalid")
    if not isinstance(value["native_dispatch"], bool):
        raise ValueError("serialized exploration vector native_dispatch is invalid")
    for field_name in ("max_results", "max_report_bytes", "evidence_version"):
        if type(value[field_name]) is not int:
            raise ValueError(f"serialized exploration vector {field_name} is invalid")
    profile = RepositoryProfileId(value["profile"])
    vector = ExplorationVectorDef(
        id=value["id"],
        disposition=ExplorationVectorDisposition(value["disposition"]),
        rationale=value["rationale"],
        applicability=ExplorationVectorApplicabilityId(value["applicability"]),
        role=value["role"],
        profile=profile,
        relationship_classes=tuple(
            RelationshipKind(item) for item in value["relationship_classes"]
        ),
        task=ExplorationTaskSpec(
            task_id=value["task_id"],
            frontier_item_id=value["frontier_item_id"],
            profile=profile,
            depends_on=tuple(value["depends_on"]),
            scope=tuple(value["scope"]),
        ),
        max_results=value["max_results"],
        max_report_bytes=value["max_report_bytes"],
        evidence_version=value["evidence_version"],
        native_dispatch=value["native_dispatch"],
        body=value["body"],
    )
    if value["digest"] != vector.digest:
        raise ValueError("serialized exploration vector digest mismatch")
    return vector


def _execution_identity_from_dict(value: object) -> ExecutionIdentity:
    if not isinstance(value, dict) or set(value) != _EXECUTION_IDENTITY_KEYS:
        raise ValueError("serialized execution identity keys are invalid")
    children = value.get("children")
    if not isinstance(children, list):
        raise ValueError("serialized execution identity children must be a list")
    scalar_values = {key: item for key, item in value.items() if key != "children"}
    if any(not isinstance(item, str) for item in scalar_values.values()):
        raise ValueError("serialized execution identity values must be text")
    parsed_children: list[ChildExecutionIdentity] = []
    for child in children:
        if (
            not isinstance(child, dict)
            or set(child) != _CHILD_EXECUTION_IDENTITY_KEYS
            or any(not isinstance(item, str) for item in child.values())
        ):
            raise ValueError("serialized child execution identity is invalid")
        parsed_children.append(ChildExecutionIdentity(**child))
    return ExecutionIdentity(**scalar_values, children=tuple(parsed_children))


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
        "exploration_vectors": {
            name: [_exploration_vector_to_dict(vector) for vector in vectors]
            for name, vectors in sorted(contract.exploration_vectors.items())
        },
        "resolved_exploration_profile": (
            contract.resolved_exploration_profile.value
            if contract.resolved_exploration_profile is not None
            else None
        ),
        "active_exploration_applicabilities": sorted(
            item.value for item in contract.active_exploration_applicabilities
        ),
        "expected_output_patterns": list(contract.expected_output_patterns),
        "write_behavior": {
            "mode": contract.write_behavior.mode,
            "expected_when": list(contract.write_behavior.expected_when),
        },
        "read_only": contract.read_only,
        "parent_sandbox_mode": contract.parent_sandbox_mode,
        "completion_required": contract.completion_required,
        "skill_contract_json": contract.skill_contract_json,
        "projection_substitutions": [list(item) for item in contract.projection_substitutions],
        "projection_gating": contract.projection_gating,
        "projection_namespace": contract.projection_namespace,
        "launch_contract": (
            json.loads(contract.launch_contract.canonical_json)
            if contract.launch_contract is not None
            else None
        ),
        "launch_contract_digest": contract.launch_contract_digest,
        "execution_identity": contract.execution_identity.to_dict(),
    }


def _contract_from_dict(data: Mapping[str, Any]) -> SkillSessionContract:
    try:
        source_refs_raw = data["source_refs"]
        if not isinstance(source_refs_raw, dict):
            raise ValueError("source_refs must be an object")
        read_only = data.get("read_only", False)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        parent_sandbox_mode = data["parent_sandbox_mode"]
        if not isinstance(parent_sandbox_mode, str):
            raise ValueError("parent_sandbox_mode must be text")
        completion_required = data.get("completion_required", False)
        if not isinstance(completion_required, bool):
            raise ValueError("completion_required must be a boolean")
        projection_gating = data.get("projection_gating")
        if projection_gating is not None and not isinstance(projection_gating, bool):
            raise ValueError("projection_gating must be a boolean or null")
        projection_substitutions = data.get("projection_substitutions", [])
        if not isinstance(projection_substitutions, list) or any(
            not isinstance(item, list) or len(item) != 2 for item in projection_substitutions
        ):
            raise ValueError("projection_substitutions entries must be two-element lists")
        launch_payload = data.get("launch_contract")
        launch_digest = data.get("launch_contract_digest", "")
        if launch_payload is not None and not isinstance(launch_payload, dict):
            raise ValueError("launch_contract must be an object or null")
        if not isinstance(launch_digest, str):
            raise ValueError("launch_contract_digest must be a string")
        launch_contract = (
            ResolvedLaunchContract.from_payload(
                launch_payload,
                expected_digest=launch_digest,
            )
            if launch_payload is not None
            else None
        )
        exploration_vectors_raw = data["exploration_vectors"]
        if not isinstance(exploration_vectors_raw, dict) or any(
            not isinstance(vectors, list) for vectors in exploration_vectors_raw.values()
        ):
            raise ValueError("exploration_vectors must be an object of lists")
        resolved_exploration_profile_raw = data["resolved_exploration_profile"]
        if resolved_exploration_profile_raw is not None and not isinstance(
            resolved_exploration_profile_raw, str
        ):
            raise ValueError("resolved_exploration_profile must be text or null")
        active_applicabilities_raw = data["active_exploration_applicabilities"]
        if not isinstance(active_applicabilities_raw, list) or any(
            not isinstance(item, str) for item in active_applicabilities_raw
        ):
            raise ValueError("active_exploration_applicabilities must be a list of text")
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
            exploration_vectors={
                str(name): tuple(_exploration_vector_from_dict(vector) for vector in vectors)
                for name, vectors in exploration_vectors_raw.items()
            },
            resolved_exploration_profile=(
                RepositoryProfileId(resolved_exploration_profile_raw)
                if resolved_exploration_profile_raw is not None
                else None
            ),
            active_exploration_applicabilities=frozenset(
                ExplorationVectorApplicabilityId(item) for item in active_applicabilities_raw
            ),
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
            read_only=read_only,
            parent_sandbox_mode=parent_sandbox_mode,
            completion_required=completion_required,
            skill_contract_json=str(data.get("skill_contract_json", "")),
            projection_substitutions=tuple(
                (str(item[0]), str(item[1])) for item in projection_substitutions
            ),
            projection_gating=projection_gating,
            projection_namespace=(
                str(data["projection_namespace"])
                if data.get("projection_namespace") is not None
                else None
            ),
            launch_contract=launch_contract,
            launch_contract_digest=launch_digest,
            execution_identity=_execution_identity_from_dict(data["execution_identity"]),
            schema_version=int(data["schema_version"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError, SkillContractError) as exc:
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
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
) -> dict[str, Any]:
    contract_data = _contract_to_dict(contract)
    return {
        "schema_version": _STORE_MANIFEST_SCHEMA_VERSION,
        "raw_session_id": raw_session_id,
        "candidate_session_ids": list(candidate_session_ids),
        "managed_lineage_ref": (
            managed_lineage_ref.to_dict() if managed_lineage_ref is not None else None
        ),
        "contract": contract_data,
        "contract_digest": _digest_json(contract_data),
        "snapshot_paths": dict(sorted(snapshot_paths.items())),
    }


def _managed_lineage_ref_from_manifest(
    manifest: Mapping[str, Any],
) -> ManagedHeadlessSessionLineageRef | None:
    if "managed_lineage_ref" not in manifest:
        raise ValueError("Skill session manifest is missing managed lineage reference")
    value = manifest.get("managed_lineage_ref")
    if value is None:
        return None
    try:
        return ManagedHeadlessSessionLineageRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid skill session managed lineage reference") from exc
