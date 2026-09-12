"""Durable retained-view reconciliation and crash recovery for Codex sessions.

The mixin stays private to the session store. It owns validation and recovery of
retained views while the store retains setup, live attempt, promotion, and index
publication ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import regex as re

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    NamedResume,
    NoResume,
    get_logger,
    is_pid_alive,
    is_session_alive,
    read_pid_namespace_inode,
)
from autoskillit.execution.backends._codex.session_attempt_lease import (
    CodexSessionAttemptLease,
)
from autoskillit.execution.backends._codex.session_storage_layout import (
    _INERT_NAMES,
    _LAUNCH_ID_RE,
    _LOCKS_SUBDIR,
    _MANIFEST_NAME,
    _MANIFEST_READ_LIMIT,
    _MANIFEST_STATES,
    _PUBLIC_TO_STORE,
    _RECONCILIATION_AUDIT_SCHEMA_VERSION,
    _STORE_TO_PUBLIC,
    _THREAD_ID_RE,
    _VIEW_ID_RE,
)
from autoskillit.execution.backends._codex_fs_atomic import (
    _atomic_json,
    _fsync_directory,
    _lexists,
    _read_bounded,
    _require_real_directory,
    _write_reconciliation_audit,
)
from autoskillit.execution.backends._codex_parse import (
    _preserves_rollout_prefix,
    _rollout_files,
    _safe_relative,
    _safe_relative_value,
    _thread_id,
)
from autoskillit.execution.backends._codex_session_lease import _FileLease
from autoskillit.execution.process import kill_process_tree

if TYPE_CHECKING:
    from autoskillit.execution.backends._codex_session_storage import CodexSessionStore

logger = get_logger(__name__)


class _CodexSessionReconciliationMixin:
    def _publish_completed_view(self, view_path: Path, parent_ids: tuple[str, ...]) -> bool:
        """Publish native child snapshots, then remove the validated completed view."""
        from autoskillit.execution.child_outcomes import collect_codex_observed_children

        store = cast("CodexSessionStore", self)
        publication_succeeded = True
        for parent_session_id in dict.fromkeys(parent_ids):
            try:
                parent_rollout_path = store.locate_session(parent_session_id)
                if parent_rollout_path is None:
                    publication_succeeded = False
                    continue
                if not collect_codex_observed_children(
                    parent_rollout_path=parent_rollout_path,
                    parent_session_id=parent_session_id,
                    log_root=store.log_dir,
                    child_rollout_resolver=store.locate_session,
                ):
                    publication_succeeded = False
            except Exception:
                publication_succeeded = False
                logger.warning(
                    "codex_completed_view_child_publication_failed",
                    view_path=str(view_path),
                    parent_session_id=parent_session_id,
                    exc_info=True,
                )
        if not publication_succeeded:
            logger.warning(
                "codex_completed_view_retained_for_child_publication",
                view_path=str(view_path),
            )
            return False

        lifecycle = _FileLease.acquire(
            store.locks_root / "lifecycle.lock",
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        )
        try:
            store._validate_completed_view(view_path)
            shutil.rmtree(view_path)
            _fsync_directory(store.views_root)
        finally:
            lifecycle.release()
        return True

    def _validate_manifest(
        self,
        view_path: Path,
        manifest: Mapping[str, Any],
    ) -> None:
        store = cast("CodexSessionStore", self)
        _require_real_directory(view_path, label="Codex recovery view")
        manifest_path = view_path / _MANIFEST_NAME
        try:
            manifest_mode = manifest_path.lstat().st_mode
        except FileNotFoundError as exc:
            raise RuntimeError("Codex recovery view has no manifest") from exc
        if stat.S_ISLNK(manifest_mode) or not stat.S_ISREG(manifest_mode):
            raise RuntimeError("Codex recovery manifest must be a regular non-symlink file")
        expected_entries = {"sessions", "archived_sessions", _MANIFEST_NAME}
        if {entry.name for entry in view_path.iterdir()} != expected_entries:
            raise RuntimeError("Codex recovery view has an invalid root layout")
        for public_name in _INERT_NAMES:
            _require_real_directory(
                view_path / public_name,
                label=f"Codex recovery {public_name} root",
            )

        if manifest.get("schema_version") != 1:
            raise RuntimeError("Unsupported Codex recovery manifest schema")
        launch_id = manifest.get("launch_id")
        attempt = manifest.get("attempt")
        view_id = manifest.get("view_id")
        if not isinstance(launch_id, str) or _LAUNCH_ID_RE.fullmatch(launch_id) is None:
            raise RuntimeError("Codex recovery manifest has an invalid launch id")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt <= 0:
            raise RuntimeError("Codex recovery manifest has an invalid attempt")
        expected_view_id = f"{launch_id}-{attempt}"
        if view_id != expected_view_id or view_path.name != expected_view_id:
            raise RuntimeError("Codex recovery manifest identity is inconsistent")

        project_cwd = manifest.get("project_cwd")
        if not isinstance(project_cwd, str):
            raise RuntimeError("Codex recovery manifest has no project discriminator")
        project_path = Path(project_cwd)
        if (
            not project_path.is_absolute()
            or str(project_path.expanduser().resolve(strict=False)) != project_cwd
        ):
            raise RuntimeError("Codex recovery project discriminator is not canonical")

        state = manifest.get("state")
        if state not in _MANIFEST_STATES:
            raise RuntimeError("Codex recovery manifest has an invalid lifecycle state")
        child_pid = manifest.get("child_pid")
        child_pgid = manifest.get("child_pgid")
        child_values = (child_pid, child_pgid)
        child_absent = child_values == (None, None)
        child_valid = all(
            not isinstance(value, bool) and isinstance(value, int) and value > 0
            for value in child_values
        )
        if not child_absent and not child_valid:
            raise RuntimeError("Codex recovery child identity is incomplete")

        reaped = manifest.get("reaped")
        if not isinstance(reaped, bool):
            raise RuntimeError("Codex recovery reap proof is not boolean")
        reaped_ns = manifest.get("reaped_ns")
        if reaped:
            if not child_valid:
                raise RuntimeError("Codex recovery reap proof has no matching child")
            if isinstance(reaped_ns, bool) or not isinstance(reaped_ns, int) or reaped_ns <= 0:
                raise RuntimeError("Codex recovery reap timestamp is invalid")
        elif reaped_ns is not None:
            raise RuntimeError("Codex recovery has a reap timestamp without proof")

        resume_thread_id = manifest.get("resume_thread_id")
        resume_store = manifest.get("resume_source_store")
        resume_relpath = manifest.get("resume_source_relpath")
        resume_values = (resume_thread_id, resume_store, resume_relpath)
        if resume_values != (None, None, None):
            if (
                not isinstance(resume_thread_id, str)
                or _THREAD_ID_RE.fullmatch(resume_thread_id) is None
                or resume_store not in _STORE_TO_PUBLIC
                or not isinstance(resume_relpath, str)
            ):
                raise RuntimeError("Codex recovery resume metadata is incomplete")
            _safe_relative_value(resume_relpath)

        final_store = manifest.get("final_store")
        final_relpath = manifest.get("final_relpath")
        if (final_store is None) != (final_relpath is None):
            raise RuntimeError("Codex recovery final metadata is incomplete")
        if final_store is not None:
            if final_store not in _STORE_TO_PUBLIC or not isinstance(final_relpath, str):
                raise RuntimeError("Codex recovery final metadata is invalid")
            final_relative = _safe_relative_value(final_relpath)
            if state not in {"finalizing", "complete"}:
                raise RuntimeError("Codex recovery final metadata precedes finalization")
            if state == "complete":
                canonical_root = (
                    store.active_root if final_store == "active" else store.archive_root
                )
                canonical = canonical_root / final_relative
                _safe_relative(canonical, canonical_root)
                final_thread_id = _thread_id(canonical)
                if final_thread_id is None or (
                    isinstance(resume_thread_id, str) and final_thread_id != resume_thread_id
                ):
                    raise RuntimeError("Codex recovery final rollout identity is invalid")
        elif state == "complete":
            raise RuntimeError("Complete Codex recovery view has no final rollout metadata")

        staged_rollouts: list[tuple[str, Path, Path, str]] = []
        for public_name, store_name in _PUBLIC_TO_STORE.items():
            staged_root = view_path / public_name
            for staged in _rollout_files(staged_root):
                relative = _safe_relative(staged, staged_root)
                thread_id = _thread_id(staged)
                if thread_id is None or _THREAD_ID_RE.fullmatch(thread_id) is None:
                    raise RuntimeError("Codex recovery staged rollout has no valid thread id")
                staged_rollouts.append((store_name, relative, staged, thread_id))

        staged_thread_ids = {item[3] for item in staged_rollouts}
        if len(staged_thread_ids) > 1:
            raise RuntimeError("Codex recovery view contains multiple thread identities")
        if isinstance(resume_thread_id, str) and any(
            thread_id != resume_thread_id for *_, thread_id in staged_rollouts
        ):
            raise RuntimeError("Codex recovery resume view changed thread identity")

        if isinstance(resume_store, str) and isinstance(resume_relpath, str):
            resume_root = store.active_root if resume_store == "active" else store.archive_root
            resume_relative = _safe_relative_value(resume_relpath)
            canonical_resume = resume_root / resume_relative
            if state in {"prepared", "running"}:
                _safe_relative(canonical_resume, resume_root)
                if not staged_rollouts or not any(
                    _preserves_rollout_prefix(canonical_resume, staged)
                    for _, _, staged, _ in staged_rollouts
                ):
                    raise RuntimeError(
                        "Codex recovery resume view does not preserve its canonical source"
                    )

        if isinstance(final_store, str) and isinstance(final_relpath, str):
            final_root = store.active_root if final_store == "active" else store.archive_root
            final_relative = _safe_relative_value(final_relpath)
            canonical_final = final_root / final_relative
            staged_final = view_path / _STORE_TO_PUBLIC[final_store] / final_relative
            final_candidates = [path for path in (staged_final, canonical_final) if _lexists(path)]
            if not final_candidates:
                raise RuntimeError("Codex recovery final rollout data is missing")
            for final_candidate in final_candidates:
                expected_root = (
                    view_path / _STORE_TO_PUBLIC[final_store]
                    if final_candidate == staged_final
                    else final_root
                )
                _safe_relative(final_candidate, expected_root)
                final_thread_id = _thread_id(final_candidate)
                if final_thread_id is None or (
                    isinstance(resume_thread_id, str) and final_thread_id != resume_thread_id
                ):
                    raise RuntimeError("Codex recovery final rollout identity is invalid")

        if state == "prepared" and (not child_absent or reaped):
            raise RuntimeError("Prepared Codex recovery view has child lifecycle data")
        if state in {"running", "finalizing", "complete"} and not child_valid:
            raise RuntimeError("Spawned Codex recovery view has no child identity")
        if state in {"finalizing", "complete"} and not reaped:
            raise RuntimeError("Final Codex recovery view has no reap proof")

    def _read_reconciliation_candidate(
        self,
        view_path: Path,
    ) -> tuple[bytes, dict[str, Any]]:
        store = cast("CodexSessionStore", self)
        raw_manifest = _read_bounded(view_path / _MANIFEST_NAME, _MANIFEST_READ_LIMIT)
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex reconciliation manifest is not valid JSON") from exc
        if not isinstance(manifest, dict):
            raise RuntimeError("Codex reconciliation manifest is not an object")
        for public_name in _INERT_NAMES:
            root = view_path / public_name
            with os.scandir(root) as entries:
                if next(entries, None) is not None:
                    raise RuntimeError(
                        f"Codex reconciliation {public_name} root is not strictly empty"
                    )
        store._validate_manifest(view_path, manifest)
        if manifest["state"] not in {"running", "finalizing", "failed"}:
            raise RuntimeError("Codex view is not a retained schema-v1 unknown attempt")
        return raw_manifest, manifest

    @staticmethod
    def _reconciliation_thread_ids(manifest: Mapping[str, Any]) -> set[str]:
        resume_thread_id = manifest.get("resume_thread_id")
        return {resume_thread_id} if isinstance(resume_thread_id, str) else set()

    def _read_reconciliation_audit(self, path: Path, *, view_id: str) -> dict[str, Any]:
        try:
            payload = json.loads(_read_bounded(path, _MANIFEST_READ_LIMIT))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid reconciliation audit for {view_id}") from exc
        expected_keys = {
            "schema_version",
            "view_id",
            "recorded_at",
            "reason",
            "manifest_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise RuntimeError(f"Invalid reconciliation audit contract for {view_id}")
        recorded_at = payload.get("recorded_at")
        reason = payload.get("reason")
        digest = payload.get("manifest_sha256")
        if (
            payload.get("schema_version") != _RECONCILIATION_AUDIT_SCHEMA_VERSION
            or payload.get("view_id") != view_id
            or not isinstance(recorded_at, str)
            or not isinstance(reason, str)
            or reason.strip() != reason
            or not reason
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise RuntimeError(f"Invalid reconciliation audit values for {view_id}")
        try:
            timestamp = datetime.fromisoformat(recorded_at)
        except ValueError as exc:
            raise RuntimeError(f"Invalid reconciliation audit timestamp for {view_id}") from exc
        if timestamp.tzinfo is None:
            raise RuntimeError(f"Reconciliation audit timestamp lacks timezone for {view_id}")
        return payload

    def list_retained_attempt_views(self) -> tuple[dict[str, Any], ...]:
        """List retained Codex attempt views without recovery or mutation."""
        store = cast("CodexSessionStore", self)
        store._ensure_roots()
        rows: list[dict[str, Any]] = []
        for view_path in sorted(store.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR:
                continue
            state: object = None
            try:
                if _VIEW_ID_RE.fullmatch(view_path.name) is None:
                    raise RuntimeError("invalid view id")
                _raw_manifest, manifest = store._read_reconciliation_candidate(view_path)
                state = manifest["state"]
            except (OSError, RuntimeError, ValueError) as exc:
                rows.append(
                    {
                        "view_id": view_path.name,
                        "state": state,
                        "eligible": False,
                        "detail": str(exc),
                    }
                )
            else:
                rows.append(
                    {
                        "view_id": view_path.name,
                        "state": state,
                        "eligible": True,
                        "detail": "retained schema-v1 unknown with empty staged roots",
                    }
                )
        return tuple(rows)

    def _delete_reconciliation_tombstone(self, tombstone_path: Path) -> None:
        store = cast("CodexSessionStore", self)
        _require_real_directory(tombstone_path, label="Codex reconciliation tombstone")
        shutil.rmtree(tombstone_path)
        _fsync_directory(store.reconciliation_tombstones_root)

    def discard_attempt_view(self, view_id: str, reason: str) -> dict[str, Any]:
        """Explicitly reconcile one eligible retained schema-v1 unknown view."""
        store = cast("CodexSessionStore", self)
        if _VIEW_ID_RE.fullmatch(view_id) is None:
            raise ValueError(f"Invalid Codex attempt view id: {view_id!r}")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("Codex attempt reconciliation requires a non-empty reason")
        store._ensure_roots()
        view_path = store.views_root / view_id
        audit_path = store.reconciliations_root / f"{view_id}.json"
        tombstone_path = store.reconciliation_tombstones_root / view_id
        view_lock = _FileLease.acquire(
            store.locks_root / f"view-{view_id}.lock",
            timeout=0.0,
        )
        thread_locks: list[_FileLease] = []
        lifecycle: _FileLease | None = None
        audit: dict[str, Any] | None = None
        delete_tombstone = False
        try:
            view_exists = _lexists(view_path)
            tombstone_exists = _lexists(tombstone_path)
            audit_exists = _lexists(audit_path)
            if view_exists and tombstone_exists:
                raise RuntimeError(f"Conflicting view and tombstone retained for {view_id}")
            if tombstone_exists:
                if not audit_exists:
                    raise RuntimeError(f"Tombstone has no reconciliation audit for {view_id}")
                _require_real_directory(tombstone_path, label="Codex reconciliation tombstone")
                audit = store._read_reconciliation_audit(audit_path, view_id=view_id)
                if audit["reason"] != normalized_reason:
                    raise RuntimeError(f"Reconciliation reason conflicts for {view_id}")
                lifecycle = _FileLease.acquire(
                    store.locks_root / "lifecycle.lock",
                    timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
                )
                if _lexists(view_path) or not _lexists(tombstone_path):
                    raise RuntimeError(f"Reconciliation tombstone changed for {view_id}")
                delete_tombstone = True
            elif not view_exists:
                if not audit_exists:
                    raise FileNotFoundError(f"Codex attempt view not found: {view_id}")
                audit = store._read_reconciliation_audit(audit_path, view_id=view_id)
                if audit["reason"] != normalized_reason:
                    raise RuntimeError(f"Reconciliation reason conflicts for {view_id}")
            else:
                initial_raw, initial_manifest = store._read_reconciliation_candidate(view_path)
                initial_digest = hashlib.sha256(initial_raw).hexdigest()
                initial_thread_ids = store._reconciliation_thread_ids(initial_manifest)
                for thread_id in sorted(initial_thread_ids):
                    thread_locks.append(
                        _FileLease.acquire(store._thread_lock_path(thread_id), timeout=0.0)
                    )
                lifecycle = _FileLease.acquire(
                    store.locks_root / "lifecycle.lock",
                    timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
                )
                if _lexists(tombstone_path):
                    raise RuntimeError(f"Reconciliation tombstone appeared for {view_id}")
                final_raw, final_manifest = store._read_reconciliation_candidate(view_path)
                final_digest = hashlib.sha256(final_raw).hexdigest()
                if (
                    final_digest != initial_digest
                    or store._reconciliation_thread_ids(final_manifest) != initial_thread_ids
                ):
                    raise RuntimeError(
                        f"Codex attempt view changed during reconciliation: {view_id}"
                    )
                if audit_exists:
                    audit = store._read_reconciliation_audit(audit_path, view_id=view_id)
                    if (
                        audit["reason"] != normalized_reason
                        or audit["manifest_sha256"] != final_digest
                    ):
                        raise RuntimeError(f"Reconciliation audit conflicts for {view_id}")
                else:
                    audit = {
                        "schema_version": _RECONCILIATION_AUDIT_SCHEMA_VERSION,
                        "view_id": view_id,
                        "recorded_at": datetime.now(UTC).isoformat(),
                        "reason": normalized_reason,
                        "manifest_sha256": final_digest,
                    }
                    _write_reconciliation_audit(audit_path, audit)
                os.rename(view_path, tombstone_path)
                _fsync_directory(store.views_root)
                _fsync_directory(store.reconciliation_tombstones_root)
                delete_tombstone = True
        finally:
            if lifecycle is not None:
                lifecycle.release()
            for thread_lock in reversed(thread_locks):
                thread_lock.release()
            view_lock.release()

        if delete_tombstone:
            store._delete_reconciliation_tombstone(tombstone_path)
        if audit is None:
            raise RuntimeError(f"Reconciliation did not produce an audit for {view_id}")
        return dict(audit)

    def recover(self) -> None:
        """Recover safely-owned orphan views, then rebuild the derived index."""
        store = cast("CodexSessionStore", self)
        store._ensure_roots()
        failures: list[BaseException] = []
        for view_path in sorted(store.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR:
                continue
            if (
                _VIEW_ID_RE.fullmatch(view_path.name) is None
                or view_path.is_symlink()
                or not view_path.is_dir()
            ):
                failures.append(RuntimeError(f"Invalid Codex recovery view retained: {view_path}"))
                continue
            lock_path = store.locks_root / f"view-{view_path.name}.lock"
            try:
                view_lock = _FileLease.acquire(lock_path, timeout=0.0)
            except TimeoutError:
                continue
            try:
                try:
                    manifest_path = view_path / _MANIFEST_NAME
                    manifest = json.loads(_read_bounded(manifest_path, _MANIFEST_READ_LIMIT))
                    if not isinstance(manifest, dict):
                        raise RuntimeError("Codex recovery manifest is not an object")
                    store._validate_manifest(view_path, manifest)
                except BaseException as exc:
                    logger.error("codex_recovery_manifest_invalid", exc_info=True)
                    failures.append(
                        RuntimeError(
                            f"Invalid Codex recovery manifest retained for {view_path.name}: {exc}"
                        )
                    )
                    continue
                state = manifest.get("state")
                thread_locks: list[_FileLease] = []
                thread_ids = {
                    thread_id
                    for public_name in _INERT_NAMES
                    for path in _rollout_files(view_path / public_name)
                    if (thread_id := _thread_id(path)) is not None
                }
                resume_thread_id = manifest.get("resume_thread_id")
                if isinstance(resume_thread_id, str):
                    thread_ids.add(resume_thread_id)
                try:
                    for thread_id in sorted(thread_ids):
                        thread_locks.append(
                            _FileLease.acquire(
                                store._thread_lock_path(thread_id),
                                timeout=0.0,
                            )
                        )
                except TimeoutError:
                    for thread_lock in reversed(thread_locks):
                        try:
                            thread_lock.release()
                        except BaseException as exc:
                            failures.append(
                                RuntimeError(
                                    f"Codex recovery thread lease release failed for "
                                    f"{view_path.name}: {exc}"
                                )
                            )
                    continue
                lifecycle: _FileLease | None = None
                parent_session_ids: tuple[str, ...] | None = None
                processing_succeeded = False
                release_succeeded = True
                try:
                    lifecycle = _FileLease.acquire(
                        store.locks_root / "lifecycle.lock",
                        timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
                    )
                    if state == "complete":
                        store._validate_completed_view(view_path)
                        final_store = manifest["final_store"]
                        final_relpath = _safe_relative_value(str(manifest["final_relpath"]))
                        final_root = (
                            store.active_root if final_store == "active" else store.archive_root
                        )
                        parent_session_id = _thread_id(final_root / final_relpath)
                        if parent_session_id is None:
                            raise RuntimeError(
                                "Completed Codex recovery view has no native parent identity"
                            )
                        parent_session_ids = (parent_session_id,)
                    elif state in {"prepared", "failed"} and manifest.get("child_pid") is None:
                        store._validate_pre_spawn_view(
                            view_path,
                            manifest,
                            allow_missing_resume=False,
                        )
                        shutil.rmtree(view_path)
                        _fsync_directory(store.views_root)
                    elif state in {"running", "finalizing", "failed"}:
                        if manifest.get("reaped") is not True:
                            child_pid = manifest.get("child_pid")
                            boot_id = manifest.get("boot_id")
                            child_starttime_ticks = manifest.get("child_starttime_ticks")
                            pidns_inode = manifest.get("pidns_inode")
                            has_identity = (
                                isinstance(child_pid, int)
                                and isinstance(boot_id, str)
                                and isinstance(child_starttime_ticks, int)
                            )
                            if has_identity:
                                assert isinstance(child_pid, int)
                                assert isinstance(boot_id, str)
                                assert isinstance(child_starttime_ticks, int)
                                identity_verified = is_session_alive(
                                    child_pid, boot_id, child_starttime_ticks
                                )
                                if identity_verified and isinstance(pidns_inode, int):
                                    actual_inode = read_pid_namespace_inode(child_pid)
                                    if actual_inode is not None and actual_inode != pidns_inode:
                                        identity_verified = False
                                if identity_verified:
                                    cleanup_result = kill_process_tree(
                                        child_pid,
                                        expected_boot_id=boot_id,
                                        expected_starttime_ticks=child_starttime_ticks,
                                    )
                                    if not cleanup_result.complete:
                                        # Fail-closed per view, fail-open at the call site —
                                        # not appended to `failures`, so an unresolvable view
                                        # never blocks cook/order startup at the two unguarded
                                        # call sites. The next recovery/chokepoint retries.
                                        logger.warning(
                                            "codex_recover_kill_incomplete",
                                            view_id=view_path.name,
                                            child_pid=child_pid,
                                        )
                                        continue
                                    manifest["reaped"] = True
                                    manifest["reaped_ns"] = time.time_ns()
                                    _atomic_json(manifest_path, manifest)
                                else:
                                    # Dead, or identity mismatch (PID recycled) — the
                                    # original child is provably gone either way.
                                    manifest["reaped"] = True
                                    manifest["reaped_ns"] = time.time_ns()
                                    _atomic_json(manifest_path, manifest)
                            elif child_pid is not None and is_pid_alive(child_pid):
                                # Legacy manifest, no identity fields: cannot verify —
                                # never kill, never lie. Operator remediation path:
                                # doctor / process-orphans --reap.
                                logger.warning(
                                    "codex_recover_legacy_manifest_live_pid",
                                    view_id=view_path.name,
                                    child_pid=child_pid,
                                )
                                manifest["state"] = "failed"
                                _atomic_json(manifest_path, manifest)
                                continue
                            else:
                                manifest["reaped"] = True
                                manifest["reaped_ns"] = time.time_ns()
                                _atomic_json(manifest_path, manifest)
                        attempt_lease = CodexSessionAttemptLease(
                            store=store,
                            session_home=Path("/"),
                            launch_id=str(manifest["launch_id"]),
                            attempt=int(manifest["attempt"]),
                            current_resume_spec=(
                                NamedResume(resume_thread_id)
                                if isinstance(resume_thread_id, str)
                                else NoResume()
                            ),
                            view_id=view_path.name,
                            view_path=view_path,
                            manifest=manifest,
                            view_lease=view_lock,
                            inert_targets={},
                        )
                        manifest["state"] = "finalizing"
                        store._write_manifest(attempt_lease)
                        recovered_rows = store._promote_view(attempt_lease)
                        store._merge_index_unlocked(recovered_rows)
                        manifest["state"] = "complete"
                        store._write_manifest(attempt_lease)
                        store._validate_completed_view(view_path)
                        parent_session_ids = tuple(
                            dict.fromkeys(str(row["session_id"]) for row in recovered_rows)
                        )
                    else:
                        raise RuntimeError(f"Unsupported Codex recovery state retained: {state!r}")
                    processing_succeeded = True
                except BaseException as exc:
                    logger.error("codex_recovery_view_failed", exc_info=True)
                    failures.append(
                        RuntimeError(f"Codex recovery failed closed for {view_path.name}: {exc}")
                    )
                finally:
                    if lifecycle is not None:
                        try:
                            lifecycle.release()
                        except BaseException as exc:
                            release_succeeded = False
                            failures.append(
                                RuntimeError(
                                    f"Codex recovery lifecycle lease release failed for "
                                    f"{view_path.name}: {exc}"
                                )
                            )
                    for thread_lock in reversed(thread_locks):
                        try:
                            thread_lock.release()
                        except BaseException as exc:
                            release_succeeded = False
                            failures.append(
                                RuntimeError(
                                    f"Codex recovery thread lease release failed for "
                                    f"{view_path.name}: {exc}"
                                )
                            )
                if processing_succeeded and release_succeeded and parent_session_ids:
                    try:
                        store._publish_completed_view(view_path, parent_session_ids)
                    except BaseException as exc:
                        logger.error("codex_recovery_publication_cleanup_failed", exc_info=True)
                        failures.append(
                            RuntimeError(
                                f"Codex recovery publication cleanup failed for "
                                f"{view_path.name}: {exc}"
                            )
                        )
            finally:
                try:
                    view_lock.release()
                except BaseException as exc:
                    failures.append(
                        RuntimeError(
                            f"Codex recovery view lease release failed for {view_path.name}: {exc}"
                        )
                    )
        lifecycle = _FileLease.acquire(
            store.locks_root / "lifecycle.lock",
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        )
        try:
            store._rebuild_index_unlocked()
        except BaseException as exc:
            logger.error("codex_recovery_index_rebuild_failed", exc_info=True)
            failures.append(RuntimeError(f"Codex recovery index rebuild failed closed: {exc}"))
        finally:
            lifecycle.release()
        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("Codex history recovery failed closed", failures)
