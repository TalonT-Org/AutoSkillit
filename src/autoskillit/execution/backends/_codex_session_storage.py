"""Private durable storage for Codex session attempt rollout views."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    CODEX_ACTIVE_VIEWS_SUBDIR,
    CODEX_ARCHIVED_SESSIONS_SUBDIR,
    CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR,
    CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR,
    CODEX_SESSIONS_SUBDIR,
    BareResume,
    NamedResume,
    NoResume,
    ResumeSpec,
    SessionSummary,
    default_log_dir,
    strict_walk,
)
from autoskillit.execution.backends._codex.session_attempt_lease import (
    CodexSessionAttemptLease,
)
from autoskillit.execution.backends._codex.session_reconciliation import (
    _CodexSessionReconciliationMixin,
)
from autoskillit.execution.backends._codex.session_storage_layout import (
    _INDEX_NAME,
    _INDEX_READ_LIMIT,
    _INERT_NAMES,
    _LOCKS_SUBDIR,
    _MANIFEST_NAME,
    _MANIFEST_READ_LIMIT,
    _PUBLIC_TO_STORE,
    _STORE_TO_PUBLIC,
    _SUPPORTED_LOCAL_FILESYSTEMS,
    _THREAD_ID_RE,
    _VIEW_ID_RE,
)
from autoskillit.execution.backends._codex_fs_atomic import (
    _atomic_json,
    _ensure_directory_chain,
    _filesystem_type,
    _fsync_directory,
    _lexists,
    _read_bounded,
    _replace_symlink,
    _require_real_directory,
)
from autoskillit.execution.backends._codex_parse import (
    _identity,
    _preserves_rollout_prefix,
    _rollout_cwd,
    _rollout_files,
    _safe_relative,
    _safe_relative_value,
    _thread_id,
)
from autoskillit.execution.backends._codex_session_lease import _FileLease
from autoskillit.execution.process import INTERACTIVE_TETHER_CEILING_SECONDS


def codex_session_index_path(log_dir: Path | None = None) -> Path:
    """Return the one production path for the derived Codex cook index."""
    root = default_log_dir() if log_dir is None else Path(log_dir)
    return root.expanduser().resolve(strict=False) / _INDEX_NAME


class CodexSessionStore(_CodexSessionReconciliationMixin):
    """Canonical rollouts, attempt views, recovery, and derived listing index."""

    def __init__(self, log_dir: Path, index_path: Path | None = None) -> None:
        self.log_dir = Path(log_dir).expanduser().resolve(strict=False)
        self.active_root = self.log_dir / CODEX_SESSIONS_SUBDIR
        self.archive_root = self.log_dir / CODEX_ARCHIVED_SESSIONS_SUBDIR
        self.views_root = self.log_dir / CODEX_ACTIVE_VIEWS_SUBDIR
        self.reconciliations_root = self.log_dir / CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR
        self.reconciliation_tombstones_root = (
            self.log_dir / CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR
        )
        self.locks_root = self.views_root / _LOCKS_SUBDIR
        self.index_path = (
            Path(index_path).expanduser().resolve(strict=False)
            if index_path is not None
            else codex_session_index_path(self.log_dir)
        )

    def _ensure_roots(self) -> None:
        roots = (
            self.active_root,
            self.archive_root,
            self.views_root,
            self.locks_root,
            self.reconciliations_root,
            self.reconciliation_tombstones_root,
        )
        for root in roots:
            root.mkdir(parents=True, exist_ok=True)
            _require_real_directory(root, label="Codex storage root")
        devices = {
            self.active_root.stat().st_dev,
            self.archive_root.stat().st_dev,
            self.views_root.stat().st_dev,
            self.reconciliations_root.stat().st_dev,
            self.reconciliation_tombstones_root.stat().st_dev,
        }
        if len(devices) != 1:
            raise RuntimeError("Codex rollout stores and views must share one filesystem")
        filesystem_types = {
            _filesystem_type(self.active_root),
            _filesystem_type(self.archive_root),
            _filesystem_type(self.views_root),
            _filesystem_type(self.reconciliations_root),
            _filesystem_type(self.reconciliation_tombstones_root),
        }
        if len(filesystem_types) != 1 or not filesystem_types <= _SUPPORTED_LOCAL_FILESYSTEMS:
            raise RuntimeError(
                "Codex durable views require one supported local filesystem; "
                f"found {sorted(filesystem_types)}"
            )

    def _thread_lock_path(self, thread_id: str) -> Path:
        if _THREAD_ID_RE.fullmatch(thread_id) is None:
            raise ValueError(f"Invalid Codex thread id: {thread_id!r}")
        key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
        return self.locks_root / f"thread-{key}.lock"

    def prepare_attempt(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
        ceiling_seconds: float = INTERACTIVE_TETHER_CEILING_SECONDS,
    ) -> CodexSessionAttemptLease:
        self._ensure_roots()
        view_id = f"{launch_id}-{attempt}"
        if _VIEW_ID_RE.fullmatch(view_id) is None:
            raise ValueError(f"Invalid Codex attempt identity: {view_id!r}")
        session_home = Path(session_home).resolve(strict=True)
        project_path = Path(project_dir)
        if not project_path.is_absolute():
            raise ValueError("Codex project directory must be absolute")
        resolved_project = project_path.resolve(strict=True)
        if resolved_project != project_path or not resolved_project.is_dir():
            raise ValueError("Codex project directory must be canonical")
        inert_targets = self._validate_inert_home(session_home)
        view_path = self.views_root / view_id
        view_lease = _FileLease.acquire(
            self.locks_root / f"view-{view_id}.lock",
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        )
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "launch_id": launch_id,
            "attempt": attempt,
            "view_id": view_id,
            "project_cwd": str(resolved_project),
            "state": "prepared",
            "child_pid": None,
            "child_pgid": None,
            "reaped": False,
            "resume_thread_id": None,
            "resume_source_store": None,
            "resume_source_relpath": None,
            "final_store": None,
            "final_relpath": None,
        }
        thread_lease: _FileLease | None = None
        view_created = False
        try:
            if os.path.lexists(view_path):
                raise FileExistsError(f"Codex attempt view already exists: {view_id}")
            view_path.mkdir(mode=0o700)
            view_created = True
            (view_path / "sessions").mkdir()
            (view_path / "archived_sessions").mkdir()
            _fsync_directory(view_path)
            _fsync_directory(self.views_root)
            if isinstance(current_resume_spec, NamedResume):
                thread_id = current_resume_spec.session_id
                thread_lease = _FileLease.acquire(
                    self._thread_lock_path(thread_id),
                    timeout=0.0,
                )
                located = self._locate_with_store(thread_id)
                if located is None:
                    raise FileNotFoundError(f"Codex resume rollout not found: {thread_id}")
                source_store, source_path = located
                source_root = self.active_root if source_store == "active" else self.archive_root
                relative = _safe_relative(source_path, source_root)
                destination_root = view_path / _STORE_TO_PUBLIC[source_store]
                destination = destination_root / relative
                _ensure_directory_chain(destination_root, relative.parent)
                os.link(source_path, destination, follow_symlinks=False)
                if _identity(destination) != _identity(source_path):
                    raise RuntimeError("Codex resume hard link identity mismatch")
                _fsync_directory(destination.parent)
                manifest.update(
                    resume_thread_id=thread_id,
                    resume_source_store=source_store,
                    resume_source_relpath=relative.as_posix(),
                )
            elif isinstance(current_resume_spec, BareResume):
                raise RuntimeError("Bare resume must be resolved before attempt preparation")
            elif not isinstance(current_resume_spec, NoResume):
                raise TypeError("Unsupported Codex resume specification")
            lease = CodexSessionAttemptLease(
                store=self,
                session_home=session_home,
                launch_id=launch_id,
                attempt=attempt,
                current_resume_spec=current_resume_spec,
                view_id=view_id,
                view_path=view_path,
                manifest=manifest,
                view_lease=view_lease,
                inert_targets=inert_targets,
                thread_lease=thread_lease,
                ceiling_seconds=ceiling_seconds,
            )
            self._write_manifest(lease)
            return lease
        except BaseException:
            if thread_lease is not None:
                thread_lease.release()
            view_lease.release()
            if view_created and _lexists(view_path):
                self._validate_pre_spawn_view(view_path, manifest, allow_missing_resume=True)
                shutil.rmtree(view_path)
                _fsync_directory(self.views_root)
            raise

    def _validate_inert_home(self, session_home: Path) -> dict[str, Path]:
        targets: dict[str, Path] = {}
        for public_name, inert_name in _INERT_NAMES.items():
            public_path = session_home / public_name
            inert_path = session_home / inert_name
            if not public_path.is_symlink():
                raise RuntimeError(f"{public_name} must be an inert symlink before view entry")
            resolved_public = public_path.resolve(strict=True)
            resolved_inert = inert_path.resolve(strict=True)
            if resolved_public != resolved_inert:
                raise RuntimeError(f"{public_name} does not resolve to its inert target")
            if not resolved_inert.is_dir() or resolved_inert.is_symlink():
                raise RuntimeError(f"Invalid inert rollout target: {resolved_inert}")
            if any(resolved_inert.iterdir()):
                raise RuntimeError(f"Inert rollout target is not empty: {resolved_inert}")
            if not resolved_inert.is_relative_to(session_home):
                raise RuntimeError("Inert rollout target escapes the generated home")
            targets[public_name] = resolved_inert
        return targets

    def _enter_attempt(self, lease: CodexSessionAttemptLease) -> None:
        self._validate_inert_home(lease.session_home)
        for public_name in _INERT_NAMES:
            _replace_symlink(
                lease.session_home / public_name,
                lease.view_path / public_name,
            )

    def _restore_inert(self, lease: CodexSessionAttemptLease) -> None:
        for public_name, target in lease.inert_targets.items():
            _replace_symlink(lease.session_home / public_name, target)
            if (lease.session_home / public_name).resolve(strict=True) != target:
                raise RuntimeError(f"Failed to restore inert {public_name} link")

    def _write_manifest(self, lease: CodexSessionAttemptLease) -> None:
        _atomic_json(lease.view_path / _MANIFEST_NAME, lease.manifest)

    def _abort_pre_spawn(self, lease: CodexSessionAttemptLease) -> None:
        lifecycle = _FileLease.acquire(
            self.locks_root / "lifecycle.lock",
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        )
        try:
            self._restore_inert(lease)
            lease.manifest["state"] = "failed"
            self._write_manifest(lease)
            self._validate_pre_spawn_view(
                lease.view_path,
                lease.manifest,
                allow_missing_resume=True,
            )
            shutil.rmtree(lease.view_path)
            _fsync_directory(self.views_root)
        finally:
            lifecycle.release()

    def _exit_attempt(self, lease: CodexSessionAttemptLease) -> None:
        if lease.manifest.get("child_pid") is None:
            self._abort_pre_spawn(lease)
            return
        self._restore_inert(lease)
        if lease.manifest.get("reaped") is not True:
            lease.manifest["state"] = "failed"
            self._write_manifest(lease)
            raise RuntimeError("Codex attempt lacks durable child-reaped proof")
        lifecycle = _FileLease.acquire(
            self.locks_root / "lifecycle.lock",
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        )
        try:
            lease.manifest["state"] = "finalizing"
            self._write_manifest(lease)
            rows = self._promote_view(lease)
            if rows:
                self._merge_index_unlocked(rows)
            lease.manifest["state"] = "complete"
            self._write_manifest(lease)
            self._validate_completed_view(lease.view_path)
            shutil.rmtree(lease.view_path)
            _fsync_directory(self.views_root)
        finally:
            lifecycle.release()

    def _validate_pre_spawn_view(
        self,
        view_path: Path,
        manifest: Mapping[str, Any],
        *,
        allow_missing_resume: bool,
    ) -> None:
        _require_real_directory(view_path, label="Codex attempt view")
        expected_root_entries = {"sessions", "archived_sessions", _MANIFEST_NAME}
        actual_root_entries = {path.name for path in view_path.iterdir()}
        if not actual_root_entries <= expected_root_entries:
            raise RuntimeError("Never-running Codex view contains unexpected root entries")
        allowed: set[tuple[str, str]] = set()
        source_store = manifest.get("resume_source_store")
        source_relpath = manifest.get("resume_source_relpath")
        if isinstance(source_store, str) and isinstance(source_relpath, str):
            allowed.add((_STORE_TO_PUBLIC[source_store], source_relpath))
        found: set[tuple[str, str]] = set()
        for public_name in _INERT_NAMES:
            root = view_path / public_name
            _require_real_directory(root, label="attempt rollout root")
            for entry in strict_walk(root):
                path = root / entry.relative_path
                if entry.kind == "l":
                    raise RuntimeError(f"Never-running Codex view contains a symlink: {path}")
                if entry.kind != "f":
                    continue
                _safe_relative_value(entry.relative_path)
                key = (public_name, entry.relative_path)
                if key not in allowed:
                    raise RuntimeError(
                        f"Never-running Codex view contains unexpected file: {path}"
                    )
                found.add(key)
        if not allow_missing_resume and found != allowed:
            raise RuntimeError("Never-running Codex view is missing its resume hard link")
        for public_name, relative_value in found:
            store_name = _PUBLIC_TO_STORE[public_name]
            canonical_root = self.active_root if store_name == "active" else self.archive_root
            relative_path = _safe_relative_value(relative_value)
            canonical = canonical_root / relative_path
            staged = view_path / public_name / relative_path
            if not canonical.exists() or _identity(canonical) != _identity(staged):
                raise RuntimeError("Resume hard link lost its canonical identity")

    def _validate_completed_view(self, view_path: Path) -> None:
        _require_real_directory(view_path, label="completed Codex attempt view")
        for public_name in _INERT_NAMES:
            root = view_path / public_name
            if not root.is_dir():
                continue
            for entry in strict_walk(root):
                if entry.kind in ("l", "f"):
                    raise RuntimeError(
                        "Completed Codex view retains unexpected data: "
                        f"{root / entry.relative_path}"
                    )

    def _promote_view(self, lease: CodexSessionAttemptLease) -> list[dict[str, Any]]:
        candidates: list[tuple[str, Path, Path, str]] = []
        for public_name, store_name in _PUBLIC_TO_STORE.items():
            view_root = lease.view_path / public_name
            for source in _rollout_files(view_root):
                relative = _safe_relative(source, view_root)
                thread_id = _thread_id(source)
                if thread_id is None:
                    raise RuntimeError(f"Rollout lacks a Codex thread id: {source}")
                candidates.append((store_name, relative, source, thread_id))

        resume_thread_id = lease.manifest.get("resume_thread_id")
        if isinstance(resume_thread_id, str) and any(
            thread_id != resume_thread_id for _, _, _, thread_id in candidates
        ):
            raise RuntimeError("Resumed Codex view contains a different thread identity")

        final_store = lease.manifest.get("final_store")
        final_relpath_value = lease.manifest.get("final_relpath")
        if (final_store is None) != (final_relpath_value is None):
            raise RuntimeError("Codex final rollout metadata is incomplete")

        selected_source: Path | None = None
        if isinstance(final_store, str) and isinstance(final_relpath_value, str):
            if final_store not in _STORE_TO_PUBLIC:
                raise RuntimeError(f"Invalid final Codex store: {final_store!r}")
            final_relative = _safe_relative_value(final_relpath_value)
            for store_name, relative, source, _ in candidates:
                if store_name == final_store and relative == final_relative:
                    selected_source = source
                    break
        else:
            if not candidates:
                raise RuntimeError("Codex attempt has no rollout data to promote")
            selectable = candidates
            resume_store = lease.manifest.get("resume_source_store")
            resume_relpath = lease.manifest.get("resume_source_relpath")
            if isinstance(resume_store, str) and isinstance(resume_relpath, str):
                transitioned = [
                    candidate
                    for candidate in candidates
                    if (candidate[0], candidate[1].as_posix()) != (resume_store, resume_relpath)
                ]
                if transitioned:
                    selectable = transitioned
            unique_locations = {
                (store_name, relative.as_posix()) for store_name, relative, _, _ in selectable
            }
            if len(unique_locations) != 1:
                raise RuntimeError("Codex rollout transition is ambiguous; preserving staged data")
            final_store, final_relative, selected_source, _ = selectable[0]

        canonical_root = self.active_root if final_store == "active" else self.archive_root
        destination = canonical_root / final_relative
        _ensure_directory_chain(canonical_root, final_relative.parent)
        if selected_source is None and not _lexists(destination):
            raise RuntimeError("Final Codex rollout is missing from staging and canonical storage")

        comparison_source = selected_source if selected_source is not None else destination
        destination_thread_id = _thread_id(comparison_source)
        if destination_thread_id is None:
            raise RuntimeError("Final Codex rollout lacks a thread identity")
        if isinstance(resume_thread_id, str) and destination_thread_id != resume_thread_id:
            raise RuntimeError("Final Codex rollout changed thread identity")

        for _, _, source, thread_id in candidates:
            if thread_id != destination_thread_id:
                raise RuntimeError("Codex view contains multiple thread identities")
            if not _preserves_rollout_prefix(source, comparison_source):
                raise RuntimeError("Codex rollout transition would discard staged rollout content")

        canonical_matches = self._canonical_matches(destination_thread_id)
        obsolete_canonical: list[Path] = []
        for _, canonical in canonical_matches:
            if canonical == destination:
                continue
            if not _preserves_rollout_prefix(canonical, comparison_source):
                raise RuntimeError(
                    "Codex rollout transition would discard canonical rollout content"
                )
            obsolete_canonical.append(canonical)

        if selected_source is not None:
            if _lexists(destination):
                if destination.is_symlink() or _identity(selected_source) != _identity(
                    destination
                ):
                    raise RuntimeError(
                        f"Codex rollout collision preserves both files: {destination}"
                    )
            else:
                try:
                    os.link(selected_source, destination, follow_symlinks=False)
                except FileExistsError:
                    if _identity(selected_source) != _identity(destination):
                        raise RuntimeError(
                            f"Codex rollout collision preserves both files: {destination}"
                        )
                if _identity(selected_source) != _identity(destination):
                    raise RuntimeError("Promoted Codex rollout identity mismatch")
                file_fd = os.open(destination, os.O_RDONLY)
                try:
                    os.fsync(file_fd)
                finally:
                    os.close(file_fd)
                _fsync_directory(destination.parent)

        destination_thread_id = _thread_id(destination)
        if destination_thread_id is None:
            raise RuntimeError("Final Codex rollout lacks a thread identity")
        if isinstance(resume_thread_id, str) and destination_thread_id != resume_thread_id:
            raise RuntimeError("Final Codex rollout changed thread identity")

        if lease.manifest.get("final_store") is None:
            lease.manifest.update(
                final_store=final_store,
                final_relpath=final_relative.as_posix(),
            )
            self._write_manifest(lease)

        for canonical in obsolete_canonical:
            if _lexists(canonical):
                canonical.unlink()
                _fsync_directory(canonical.parent)

        for _, _, source, _ in candidates:
            if _lexists(source):
                source.unlink()
                _fsync_directory(source.parent)

        remaining = [
            path
            for public_name in _INERT_NAMES
            for path in _rollout_files(lease.view_path / public_name)
        ]
        if remaining:
            raise RuntimeError("Codex view retains rollout data after promotion")

        return [
            self._index_row(
                thread_id=destination_thread_id,
                launch_id=lease.launch_id,
                cwd=str(lease.manifest["project_cwd"]),
                canonical_store=final_store,
                relative_path=final_relative,
            )
        ]

    def _index_row(
        self,
        *,
        thread_id: str,
        launch_id: str | None,
        cwd: str,
        canonical_store: str,
        relative_path: Path,
    ) -> dict[str, Any]:
        return {
            "backend_name": "codex",
            "session_id": thread_id,
            "launch_id": launch_id,
            "cwd": cwd,
            "first_prompt": "",
            "summary": "",
            "git_branch": None,
            "modified": None,
            "is_sidechain": False,
            "session_type_hint": "cook",
            "canonical_store": canonical_store,
            "relative_path": relative_path.as_posix(),
        }

    def _read_index_rows(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            raw = _read_bounded(self.index_path, _INDEX_READ_LIMIT)
            payload = json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [dict(row) for row in payload if isinstance(row, Mapping)]

    def _merge_index(self, incoming: Sequence[dict[str, Any]]) -> None:
        lifecycle = _FileLease.acquire(
            self.locks_root / "lifecycle.lock",
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
        )
        try:
            self._merge_index_unlocked(incoming)
        finally:
            lifecycle.release()

    def _merge_index_unlocked(self, incoming: Sequence[dict[str, Any]]) -> None:
        existing = self._read_index_rows()
        existing_by_id = {
            str(row["session_id"]): row
            for row in existing
            if isinstance(row.get("session_id"), str)
        }
        incoming_ids = {
            str(row["session_id"]) for row in incoming if isinstance(row.get("session_id"), str)
        }
        if len(incoming_ids) != len(incoming):
            raise RuntimeError("Codex index update contains duplicate or invalid session ids")
        ordered: list[dict[str, Any]] = []
        for row in incoming:
            merged = dict(row)
            session_id = str(merged["session_id"])
            previous = existing_by_id.get(session_id)
            if (
                merged.get("launch_id") is None
                and previous is not None
                and isinstance(previous.get("launch_id"), str)
            ):
                merged["launch_id"] = previous["launch_id"]
            ordered.append(merged)
        ordered.extend(
            row
            for row in existing
            if isinstance(row.get("session_id"), str)
            and str(row["session_id"]) not in incoming_ids
        )
        _atomic_json(self.index_path, ordered)

    def _rebuild_index_unlocked(self) -> None:
        existing_by_id = {
            str(row["session_id"]): row
            for row in self._read_index_rows()
            if isinstance(row.get("session_id"), str)
        }
        rebuilt: list[dict[str, Any]] = []
        seen: dict[str, Path] = {}
        for store_name, root in (
            ("active", self.active_root),
            ("archived", self.archive_root),
        ):
            for path in _rollout_files(root):
                thread_id = _thread_id(path)
                cwd = _rollout_cwd(path)
                if thread_id is None or cwd is None:
                    continue
                previous_path = seen.get(thread_id)
                if previous_path is not None:
                    raise RuntimeError(
                        "Cannot rebuild Codex index from ambiguous canonical "
                        f"representations: {previous_path}, {path}"
                    )
                seen[thread_id] = path
                existing = existing_by_id.get(thread_id)
                launch_id = (
                    str(existing["launch_id"])
                    if existing is not None and isinstance(existing.get("launch_id"), str)
                    else None
                )
                rebuilt.append(
                    self._index_row(
                        thread_id=thread_id,
                        launch_id=launch_id,
                        cwd=cwd,
                        canonical_store=store_name,
                        relative_path=_safe_relative(path, root),
                    )
                )
        _atomic_json(self.index_path, rebuilt)

    def read_index(self, cwd: str) -> tuple[SessionSummary, ...]:
        wanted = str(Path(cwd).expanduser().resolve(strict=False))
        summaries: list[SessionSummary] = []
        for row in self._read_index_rows():
            try:
                row_cwd_raw = row.get("cwd")
                if not isinstance(row_cwd_raw, str) or not row_cwd_raw:
                    continue
                row_cwd = str(Path(row_cwd_raw).expanduser().resolve(strict=False))
                if row_cwd != wanted:
                    continue
                summary = SessionSummary(
                    backend_name=str(row.get("backend_name") or "codex"),
                    session_id=str(row["session_id"]),
                    launch_id=(
                        str(row["launch_id"]) if row.get("launch_id") is not None else None
                    ),
                    cwd=row_cwd,
                    first_prompt=str(row.get("first_prompt") or ""),
                    summary=str(row.get("summary") or ""),
                    git_branch=(
                        str(row["git_branch"]) if row.get("git_branch") is not None else None
                    ),
                    modified=(str(row["modified"]) if row.get("modified") is not None else None),
                    is_sidechain=bool(row.get("is_sidechain", False)),
                    session_type_hint=(
                        str(row["session_type_hint"])
                        if row.get("session_type_hint") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if not summary.is_sidechain:
                summaries.append(summary)
        return tuple(summaries)

    def _canonical_matches(self, thread_id: str) -> list[tuple[str, Path]]:
        matches: list[tuple[str, Path]] = []
        for store_name, root in (
            ("active", self.active_root),
            ("archived", self.archive_root),
        ):
            for path in _rollout_files(root):
                if _thread_id(path) == thread_id:
                    matches.append((store_name, path))
        return matches

    def _locate_with_store(self, thread_id: str) -> tuple[str, Path] | None:
        matches = self._canonical_matches(thread_id)
        if len(matches) > 1:
            locations = ", ".join(str(path) for _, path in matches)
            raise RuntimeError(
                f"Ambiguous canonical Codex rollout representations for {thread_id}: {locations}"
            )
        return matches[0] if matches else None

    def locate_session(self, thread_id: str) -> Path | None:
        located = self._locate_with_store(thread_id)
        if located is not None:
            return located[1]
        if not self.views_root.is_dir():
            return None
        for view_path in sorted(self.views_root.iterdir()):
            if view_path.name == _LOCKS_SUBDIR or not view_path.is_dir():
                continue
            try:
                manifest = json.loads(
                    _read_bounded(view_path / _MANIFEST_NAME, _MANIFEST_READ_LIMIT)
                )
                if not isinstance(manifest, dict):
                    continue
                self._validate_manifest(view_path, manifest)
                if manifest["state"] not in {"running", "finalizing"}:
                    continue
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
                continue
            for public_name in _INERT_NAMES:
                for path in _rollout_files(view_path / public_name):
                    if _thread_id(path) == thread_id:
                        return path
        return None


__all__ = [
    "CodexSessionAttemptLease",
    "CodexSessionStore",
    "codex_session_index_path",
]
