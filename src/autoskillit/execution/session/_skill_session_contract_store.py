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
    RelationshipKind,
    RepositoryProfileId,
    ResolvedLaunchContract,
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
        # Raw pre-gate: reject stale projection versions before _contract_from_dict
        # tries to construct enum values that may no longer exist.
        raw_projection_version = contract_data.get("projection_version")
        if (
            type(raw_projection_version) is not int
            or raw_projection_version != SKILL_PROJECTION_VERSION
        ):
            raise ValueError(
                f"unsupported projection_version {raw_projection_version}; "
                f"expected {SKILL_PROJECTION_VERSION}"
            )
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


# Codec helpers live in _skill_session_contract_codec.py; re-exported for
# existing callers using the canonical _skill_session_contract_store path
# (notably tests at tests/execution/test_skill_session_contract_store.py and
# tests/server/test_run_skill_resume.py that import _digest_json).
from autoskillit.execution.session._skill_session_contract_codec import (  # noqa: F401
    _build_manifest,
    _contract_from_dict,
    _contract_to_dict,
    _delete_finalized_contract,
    _digest_json,
    _execution_identity_from_dict,
    _exploration_vector_from_dict,
    _exploration_vector_to_dict,
    _finalized_contract_path,
    _managed_lineage_ref_from_manifest,
    _source_ref_from_dict,
    _source_ref_to_dict,
    _validate_contract,
    _validate_digest_map,
    _validate_raw_session_id,
    _validate_relative_path,
    _validate_snapshot_mapping,
)

