"""Durable Codex rollout view, promotion, and recovery contracts."""

from __future__ import annotations

import json
import multiprocessing
import os
import plistlib
import threading
from pathlib import Path

import pytest
import structlog
import zstandard

from autoskillit.core import NamedResume, NoResume
from autoskillit.execution.backends import _codex_session_storage as storage
from autoskillit.execution.backends._codex_session_storage import (
    CodexInteractiveSessionLease,
    CodexSessionStore,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _generated_home(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    home = tmp_path / "generated-home"
    home.mkdir()
    inert_targets: dict[str, Path] = {}
    for name in ("sessions", "archived_sessions"):
        target = home / f".inert-{name}"
        target.mkdir()
        (home / name).symlink_to(target)
        inert_targets[name] = target.resolve()
    return home, inert_targets


def _rollout(path: Path, thread_id: str, *, cwd: Path | None = None) -> bytes:
    rows = [f'{{"type":"thread.started","thread_id":"{thread_id}"}}']
    if cwd is not None:
        rows.append(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": thread_id, "cwd": str(cwd)},
                }
            )
        )
    rows.append('{"type":"turn.completed"}')
    content = ("\n".join(rows) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _hold_flock(lock_path: str, ready: object, release: object) -> None:
    import fcntl

    ready_event = ready
    release_event = release
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        ready_event.set()  # type: ignore[attr-defined]
        if not release_event.wait(10):  # type: ignore[attr-defined]
            raise RuntimeError("timed out waiting to release lifecycle test lock")
    finally:
        os.close(descriptor)


def _retained_empty_unknown_view(
    tmp_path: Path,
) -> tuple[CodexSessionStore, Path]:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )
    with pytest.raises(RuntimeError, match="no rollout data"):
        with lease as handle:
            handle.record_spawn(os.getpid(), os.getpgrp())
            handle.record_reaped(os.getpid(), os.getpgrp())
    return store, lease.view_path


def test_file_lease_rejects_non_lock_path(tmp_path: Path) -> None:
    invalid_path = tmp_path / "lease"

    with pytest.raises(ValueError, match=r"\.lock suffix"):
        storage._FileLease.acquire(invalid_path)

    assert not invalid_path.exists()


def test_file_lease_rejects_non_regular_lock_inode(tmp_path: Path) -> None:
    lock_path = tmp_path / "lease.lock"
    os.mkfifo(lock_path)

    with pytest.raises(RuntimeError, match="not a regular file"):
        storage._FileLease.acquire(lock_path)


def test_file_lease_closes_descriptor_when_owner_diagnostic_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "lease.lock"

    def fail_ftruncate(_fd: int, _length: int) -> None:
        raise OSError("diagnostic write failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(storage.os, "ftruncate", fail_ftruncate)
        with pytest.raises(OSError, match="diagnostic write failed"):
            storage._FileLease.acquire(lock_path)

    lease = storage._FileLease.acquire(lock_path, nonblocking=True)
    lease.release()


def test_fresh_attempt_exposes_empty_view_and_no_child_abort_restores_inert_links(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    index_path = log_dir / "codex-session-index.json"
    store = CodexSessionStore(log_dir=log_dir, index_path=index_path)
    home, inert_targets = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )

    assert isinstance(lease, CodexInteractiveSessionLease)
    with lease as handle:
        assert handle.view_id
        assert (home / "sessions").resolve().parent.name == handle.view_id
        assert (home / "archived_sessions").resolve().parent.name == handle.view_id
        assert list((home / "sessions").resolve().iterdir()) == []
        assert list((home / "archived_sessions").resolve().iterdir()) == []

    assert (home / "sessions").resolve() == inert_targets["sessions"]
    assert (home / "archived_sessions").resolve() == inert_targets["archived_sessions"]
    assert {path.name for path in (log_dir / "codex-active-sessions").glob("*")} == {".locks"}
    assert list((log_dir / "codex-sessions").rglob("*.jsonl")) == []
    assert not index_path.exists()


def test_resume_archive_transition_leaves_exactly_one_canonical_rollout(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    relative = Path("2026/07/rollout-resume.jsonl")
    active = log_dir / "codex-sessions" / relative
    _rollout(active, "thread-resume")
    original_identity = (active.stat().st_dev, active.stat().st_ino)
    home, _ = _generated_home(tmp_path)

    with store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NamedResume("thread-resume"),
    ) as handle:
        staged_active = (home / "sessions").resolve() / relative
        staged_archive = (home / "archived_sessions").resolve() / relative
        staged_archive.parent.mkdir(parents=True)
        staged_active.rename(staged_archive)
        handle.record_spawn(os.getpid(), os.getpgrp())
        handle.record_reaped(os.getpid(), os.getpgrp())

    archived = log_dir / "codex-archived-sessions" / relative
    assert not active.exists()
    assert archived.is_file()
    assert (archived.stat().st_dev, archived.stat().st_ino) == original_identity


def test_resume_representation_transition_retires_old_jsonl(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    relative = Path("2026/07/rollout-resume.jsonl")
    active = log_dir / "codex-sessions" / relative
    content = _rollout(active, "thread-resume")
    home, _ = _generated_home(tmp_path)

    with store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NamedResume("thread-resume"),
    ) as handle:
        staged_jsonl = (home / "sessions").resolve() / relative
        staged_zst = staged_jsonl.with_suffix(".jsonl.zst")
        staged_jsonl.unlink()
        staged_zst.write_bytes(zstandard.ZstdCompressor().compress(content))
        handle.record_spawn(os.getpid(), os.getpgrp())
        handle.record_reaped(os.getpid(), os.getpgrp())

    compressed = active.with_suffix(".jsonl.zst")
    assert not active.exists()
    assert zstandard.ZstdDecompressor().decompress(compressed.read_bytes()) == content


def test_resume_representation_transition_preserves_both_when_content_would_be_lost(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    relative = Path("2026/07/rollout-resume.jsonl")
    active = log_dir / "codex-sessions" / relative
    original = _rollout(active, "thread-resume")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NamedResume("thread-resume"),
    )

    with pytest.raises(RuntimeError, match="discard canonical rollout content"):
        with lease as handle:
            staged_jsonl = (home / "sessions").resolve() / relative
            staged_zst = staged_jsonl.with_suffix(".jsonl.zst")
            staged_jsonl.unlink()
            truncated = b'{"type":"thread.started","thread_id":"thread-resume"}\n'
            staged_zst.write_bytes(zstandard.ZstdCompressor().compress(truncated))
            handle.record_spawn(os.getpid(), os.getpgrp())
            handle.record_reaped(os.getpid(), os.getpgrp())

    assert active.read_bytes() == original
    staged_zst = lease.view_path / "sessions" / relative.with_suffix(".jsonl.zst")
    assert staged_zst.is_file()
    assert zstandard.ZstdDecompressor().decompress(staged_zst.read_bytes()) != original


def test_missing_running_rollout_fails_closed_and_recovery_retains_view(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )

    with pytest.raises(RuntimeError, match="no rollout data"):
        with lease as handle:
            handle.record_spawn(os.getpid(), os.getpgrp())
            handle.record_reaped(os.getpid(), os.getpgrp())

    manifest_path = lease.view_path / "manifest.json"
    assert manifest_path.is_file()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "finalizing"
    with pytest.raises(RuntimeError, match="failed closed"):
        store.recover()
    assert lease.view_path.is_dir()


def test_attempt_cleanup_log_carries_context_without_owning_the_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )
    lease.__enter__()

    def fail_cleanup(_lease: CodexInteractiveSessionLease) -> None:
        provider_secret = "cleanup-secret-4361"
        assert provider_secret
        raise RuntimeError("controlled cleanup failure")

    monkeypatch.setattr(store, "_exit_attempt", fail_cleanup)
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(RuntimeError, match="controlled cleanup failure"):
            lease.__exit__(None, None, None)

    event = next(log for log in logs if log["event"] == "codex_attempt_exit_failed")
    assert event["view_id"] == lease.view_id
    assert event["error_type"] == "RuntimeError"
    assert "exc_info" not in event
    assert "cleanup-secret-4361" not in json.dumps(event)


def test_explicit_reconciliation_lists_and_discards_only_selected_empty_unknown(
    tmp_path: Path,
) -> None:
    store, view_path = _retained_empty_unknown_view(tmp_path)
    manifest_before = (view_path / "manifest.json").read_bytes()

    assert store.list_attempt_reconciliations() == (
        {
            "view_id": view_path.name,
            "state": "finalizing",
            "eligible": True,
            "detail": "retained schema-v1 unknown with empty staged roots",
        },
    )
    assert (view_path / "manifest.json").read_bytes() == manifest_before

    audit = store.discard_attempt_view(view_path.name, "  operator reviewed issue #4361  ")

    assert audit["reason"] == "operator reviewed issue #4361"
    assert audit["manifest_sha256"] == storage.hashlib.sha256(manifest_before).hexdigest()
    assert not view_path.exists()
    assert not (store.reconciliation_tombstones_root / view_path.name).exists()
    audit_path = store.reconciliations_root / f"{view_path.name}.json"
    assert json.loads(audit_path.read_text(encoding="utf-8")) == audit
    assert list(store.active_root.rglob("*.jsonl*")) == []
    assert list(store.archive_root.rglob("*.jsonl*")) == []
    assert not store.index_path.exists()


@pytest.mark.parametrize(
    "debris_kind",
    [
        "hidden-file",
        "rollout-file",
        "ordinary-file",
        "nested-directory",
        "symlink",
        "broken-symlink",
    ],
)
def test_reconciliation_rejects_every_staged_descendant_kind(
    tmp_path: Path,
    debris_kind: str,
) -> None:
    store, view_path = _retained_empty_unknown_view(tmp_path)
    staged_root = view_path / "sessions"
    if debris_kind == "hidden-file":
        (staged_root / ".hidden").write_text("retained", encoding="utf-8")
    elif debris_kind == "rollout-file":
        _rollout(staged_root / "rollout.jsonl", "thread-retained")
    elif debris_kind == "ordinary-file":
        (staged_root / "notes.txt").write_text("retained", encoding="utf-8")
    elif debris_kind == "nested-directory":
        (staged_root / "nested").mkdir()
    else:
        target = tmp_path / "target"
        if debris_kind == "symlink":
            target.write_text("outside", encoding="utf-8")
        (staged_root / "link").symlink_to(target)

    with pytest.raises(RuntimeError, match="not strictly empty"):
        store.discard_attempt_view(view_path.name, "reviewed")

    assert view_path.is_dir()
    assert not (store.reconciliations_root / f"{view_path.name}.json").exists()
    assert not (store.reconciliation_tombstones_root / view_path.name).exists()


def test_reconciliation_fails_closed_when_manifest_changes_under_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, view_path = _retained_empty_unknown_view(tmp_path)
    original_read = store._read_reconciliation_candidate
    calls = 0

    def change_after_initial_read(path: Path) -> tuple[bytes, dict[str, object]]:
        nonlocal calls
        raw, manifest = original_read(path)
        calls += 1
        if calls == 1:
            changed = dict(manifest)
            changed["reaped_ns"] = int(changed["reaped_ns"]) + 1
            storage._atomic_json(path / "manifest.json", changed)
        return raw, manifest

    monkeypatch.setattr(store, "_read_reconciliation_candidate", change_after_initial_read)

    with pytest.raises(RuntimeError, match="changed during reconciliation"):
        store.discard_attempt_view(view_path.name, "reviewed")

    assert view_path.is_dir()
    assert not (store.reconciliations_root / f"{view_path.name}.json").exists()


def test_reconciliation_retry_resumes_tombstone_deletion_and_preserves_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, view_path = _retained_empty_unknown_view(tmp_path)
    original_delete = store._delete_reconciliation_tombstone

    with monkeypatch.context() as scoped:
        scoped.setattr(
            store,
            "_delete_reconciliation_tombstone",
            lambda _path: (_ for _ in ()).throw(OSError("injected deletion failure")),
        )
        with pytest.raises(OSError, match="injected deletion failure"):
            store.discard_attempt_view(view_path.name, "reviewed")

    tombstone = store.reconciliation_tombstones_root / view_path.name
    audit_path = store.reconciliations_root / f"{view_path.name}.json"
    audit_before = audit_path.read_bytes()
    assert tombstone.is_dir()
    assert not view_path.exists()
    with pytest.raises(RuntimeError, match="reason conflicts"):
        store.discard_attempt_view(view_path.name, "different reason")
    assert tombstone.is_dir()
    assert audit_path.read_bytes() == audit_before

    monkeypatch.setattr(store, "_delete_reconciliation_tombstone", original_delete)
    retried = store.discard_attempt_view(view_path.name, "reviewed")

    assert not tombstone.exists()
    assert audit_path.read_bytes() == audit_before
    assert retried == json.loads(audit_before)
    assert store.discard_attempt_view(view_path.name, "reviewed") == retried


@pytest.mark.parametrize(
    "failure_point",
    ["audit-publish", "rename", "views-fsync", "tombstone-fsync"],
)
def test_reconciliation_crash_boundaries_leave_a_retryable_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    store, view_path = _retained_empty_unknown_view(tmp_path)
    audit_path = store.reconciliations_root / f"{view_path.name}.json"
    tombstone = store.reconciliation_tombstones_root / view_path.name
    original_fsync = storage._fsync_directory

    with monkeypatch.context() as scoped:
        if failure_point == "audit-publish":
            scoped.setattr(
                storage,
                "_write_reconciliation_audit",
                lambda _path, _payload: (_ for _ in ()).throw(
                    OSError("injected audit-publish failure")
                ),
            )
        elif failure_point == "rename":
            scoped.setattr(
                storage.os,
                "rename",
                lambda _source, _target: (_ for _ in ()).throw(OSError("injected rename failure")),
            )
        else:

            def fail_target_fsync(path: Path) -> None:
                target = (
                    store.views_root
                    if failure_point == "views-fsync"
                    else store.reconciliation_tombstones_root
                )
                if path == target and tombstone.exists():
                    raise OSError(f"injected {failure_point} failure")
                original_fsync(path)

            scoped.setattr(storage, "_fsync_directory", fail_target_fsync)

        with pytest.raises(OSError, match=f"injected {failure_point} failure"):
            store.discard_attempt_view(view_path.name, "reviewed")

    if failure_point == "audit-publish":
        assert view_path.is_dir()
        assert not audit_path.exists()
        assert not tombstone.exists()
    elif failure_point == "rename":
        assert view_path.is_dir()
        assert audit_path.is_file()
        assert not tombstone.exists()
    else:
        assert not view_path.exists()
        assert audit_path.is_file()
        assert tombstone.is_dir()

    audit = store.discard_attempt_view(view_path.name, "reviewed")
    assert audit["view_id"] == view_path.name
    assert not view_path.exists()
    assert not tombstone.exists()


@pytest.mark.parametrize(
    ("view_id", "reason", "message"),
    [
        ("not-a-view", "reviewed", "Invalid Codex attempt view id"),
        ("0123456789abcdef-1", "   ", "requires a non-empty reason"),
    ],
)
def test_reconciliation_rejects_invalid_authority_before_storage_mutation(
    tmp_path: Path,
    view_id: str,
    reason: str,
    message: str,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)

    with pytest.raises(ValueError, match=message):
        store.discard_attempt_view(view_id, reason)

    assert not log_dir.exists()


def test_recovery_rejects_incomplete_manifest_without_deleting_view(
    tmp_path: Path,
) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )
    lease.view_lease.release()
    manifest_path = lease.view_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["project_cwd"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid Codex recovery manifest"):
        store.recover()
    assert lease.view_path.is_dir()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("launch_id", "NOT-CANONICAL"),
        ("attempt", 0),
        ("attempt", True),
        ("view_id", "0123456789abcdef-2"),
        ("project_cwd", "relative/project"),
        ("state", "unknown"),
        ("child_pid", True),
        ("reaped", 1),
        ("resume_thread_id", "thread-without-source-fields"),
        ("final_store", "active"),
    ],
)
def test_recovery_manifest_validation_rejects_invalid_contract_fields(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )
    lease.view_lease.release()
    manifest_path = lease.view_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = invalid_value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid Codex recovery manifest"):
        store.recover()

    assert lease.view_path.is_dir()


def test_recovery_manifest_validation_rejects_symlinked_rollout_data(
    tmp_path: Path,
) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )
    lease.view_lease.release()
    outside = tmp_path / "outside.jsonl"
    _rollout(outside, "thread-symlink")
    (lease.view_path / "sessions" / "rollout-symlink.jsonl").symlink_to(outside)

    with pytest.raises(RuntimeError, match="Invalid Codex recovery manifest"):
        store.recover()

    assert lease.view_path.is_dir()


def test_locator_rejects_ambiguous_canonical_representations(tmp_path: Path) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    relative = Path("2026/07/rollout-ambiguous.jsonl")
    _rollout(store.active_root / relative, "thread-ambiguous")
    _rollout(store.archive_root / relative, "thread-ambiguous")

    with pytest.raises(RuntimeError, match="Ambiguous canonical"):
        store.locate_session("thread-ambiguous")


def test_locator_ignores_view_with_invalid_manifest(tmp_path: Path) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )
    lease.view_lease.release()
    _rollout(lease.view_path / "sessions" / "rollout-invalid.jsonl", "thread-invalid")
    manifest_path = lease.view_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["launch_id"] = "invalid"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert store.locate_session("thread-invalid") is None


def test_recovery_scans_and_rebuilds_index_inside_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    store._ensure_roots()
    first = store.active_root / "2026/07/rollout-first.jsonl"
    _rollout(first, "thread-first", cwd=tmp_path)
    store.index_path.write_text(
        json.dumps([{"session_id": "thread-first", "launch_id": "0123456789abcdef"}]),
        encoding="utf-8",
    )

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_flock,
        args=(str(store.locks_root / "lifecycle.lock"), ready, release),
    )
    holder.start()
    recovery: threading.Thread | None = None
    failures: list[BaseException] = []
    try:
        assert ready.wait(5)

        lifecycle_requested = threading.Event()
        original_acquire = storage._FileLease.acquire.__func__

        def notifying_acquire(
            cls: type[storage._FileLease],
            path: Path,
            *,
            nonblocking: bool = False,
        ) -> storage._FileLease:
            if path.name == "lifecycle.lock":
                lifecycle_requested.set()
            return original_acquire(cls, path, nonblocking=nonblocking)

        monkeypatch.setattr(storage._FileLease, "acquire", classmethod(notifying_acquire))

        def recover() -> None:
            try:
                store.recover()
            except BaseException as exc:
                failures.append(exc)

        recovery = threading.Thread(target=recover, daemon=True)
        recovery.start()
        assert lifecycle_requested.wait(5)
        second = store.archive_root / "2026/07/rollout-second.jsonl"
        _rollout(second, "thread-second", cwd=tmp_path)
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
        if recovery is not None:
            recovery.join(10)

    assert recovery is not None
    assert not recovery.is_alive()
    assert holder.exitcode == 0
    assert failures == []
    rows = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert {row["session_id"] for row in rows} == {"thread-first", "thread-second"}
    first_row = next(row for row in rows if row["session_id"] == "thread-first")
    assert first_row["launch_id"] == "0123456789abcdef"


def test_view_lease_is_acquired_before_view_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    home, _ = _generated_home(tmp_path)
    observed: list[bool] = []
    original_acquire = storage._FileLease.acquire.__func__

    def recording_acquire(
        cls: type[storage._FileLease],
        path: Path,
        *,
        nonblocking: bool = False,
    ) -> storage._FileLease:
        if path.name.startswith("view-"):
            observed.append(not (store.views_root / "0123456789abcdef-1").exists())
        return original_acquire(cls, path, nonblocking=nonblocking)

    monkeypatch.setattr(
        storage._FileLease,
        "acquire",
        classmethod(recording_acquire),
    )
    with store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    ):
        pass

    assert observed == [True]


def test_recovery_rebuild_preserves_rollout_project_discriminator(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    canonical = log_dir / "codex-sessions" / "2026/07/rollout.jsonl"
    _rollout(canonical, "thread-project", cwd=tmp_path)

    store.recover()

    summaries = store.read_index(str(tmp_path))
    assert [(summary.session_id, summary.cwd) for summary in summaries] == [
        ("thread-project", str(tmp_path))
    ]


def test_unsupported_filesystem_fails_before_view_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    home, _ = _generated_home(tmp_path)
    monkeypatch.setattr(storage, "_filesystem_type", lambda _path: "nfs")

    with pytest.raises(RuntimeError, match="supported local filesystem"):
        store.prepare_attempt(
            session_home=home,
            project_dir=tmp_path,
            launch_id="0123456789abcdef",
            attempt=1,
            current_resume_spec=NoResume(),
        )

    assert not (log_dir / "codex-active-sessions" / "0123456789abcdef-1").exists()


def test_darwin_filesystem_classification_uses_diskutil(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    mount_root = tmp_path / "volume-root"
    result = storage.subprocess.CompletedProcess(
        args=(),
        returncode=0,
        stdout=plistlib.dumps({"FilesystemType": "apfs"}),
        stderr=b"",
    )

    def run(command: tuple[str, ...], **kwargs: object):
        calls.append((command, kwargs))
        return result

    monkeypatch.setattr(storage.sys, "platform", "darwin")
    monkeypatch.setattr(storage.subprocess, "run", run)
    monkeypatch.setattr(storage, "_filesystem_mount_root", lambda _path: mount_root)

    assert storage._filesystem_type(tmp_path) == "apfs"
    assert calls == [
        (
            ("/usr/sbin/diskutil", "info", "-plist", str(mount_root)),
            {"capture_output": True, "check": False, "timeout": 5},
        )
    ]


def test_filesystem_mount_root_resolves_and_stops_at_device_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    volume_root = tmp_path / "volume-root"
    nested = volume_root / "one" / "two"
    nested.mkdir(parents=True)
    alias = tmp_path / "volume-alias"
    alias.symlink_to(nested, target_is_directory=True)
    resolved_nested = nested.resolve()
    devices = {
        resolved_nested: 17,
        resolved_nested.parent: 17,
        volume_root.resolve(): 17,
        tmp_path.resolve(): 23,
    }
    original_stat = Path.stat

    class DeviceStat:
        def __init__(self, device: int) -> None:
            self.st_dev = device

    def controlled_stat(path: Path, *args: object, **kwargs: object):
        if path in devices:
            return DeviceStat(devices[path])
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", controlled_stat)

    assert storage._filesystem_mount_root(alias) == volume_root.resolve()


def test_filesystem_mount_root_terminates_at_filesystem_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/")
    monkeypatch.setattr(Path, "resolve", lambda _path, *, strict: root)
    monkeypatch.setattr(Path, "stat", lambda _path: type("Stat", (), {"st_dev": 17})())

    assert storage._filesystem_mount_root(tmp_path) == root


def test_cross_device_layout_fails_before_view_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    home, _ = _generated_home(tmp_path)
    store._ensure_roots()
    original_stat = Path.stat

    class _DifferentDevice:
        def __init__(self, result: os.stat_result) -> None:
            self._result = result
            self.st_dev = result.st_dev + 1

        def __getattr__(self, name: str) -> object:
            return getattr(self._result, name)

    def different_archive_device(path: Path, *args: object, **kwargs: object) -> object:
        result = original_stat(path, *args, **kwargs)
        if path == store.archive_root:
            return _DifferentDevice(result)
        return result

    monkeypatch.setattr(Path, "stat", different_archive_device)
    with pytest.raises(RuntimeError, match="share one filesystem"):
        store.prepare_attempt(
            session_home=home,
            project_dir=tmp_path,
            launch_id="0123456789abcdef",
            attempt=1,
            current_resume_spec=NoResume(),
        )

    assert not (store.views_root / "0123456789abcdef-1").exists()


def test_promotion_collision_preserves_canonical_and_staged_rollouts(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    relative = Path("2026/07/rollout-collision.jsonl")
    canonical = store.active_root / relative
    canonical_bytes = _rollout(canonical, "thread-existing")
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )

    with pytest.raises(RuntimeError, match="collision preserves both files"):
        with lease as handle:
            staged = (home / "sessions").resolve() / relative
            staged_bytes = _rollout(staged, "thread-new")
            handle.record_spawn(os.getpid(), os.getpgrp())
            handle.record_reaped(os.getpid(), os.getpgrp())

    assert canonical.read_bytes() == canonical_bytes
    assert (lease.view_path / "sessions" / relative).read_bytes() == staged_bytes


def test_recovery_skips_live_attempt_until_inherited_view_lease_is_released(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )

    handle = lease.__enter__()
    relative = Path("2026/07/rollout-live.jsonl")
    _rollout((home / "sessions").resolve() / relative, "thread-live")
    handle.record_spawn(os.getpid(), os.getpgrp())
    store.recover()
    assert lease.view_path.is_dir()
    assert not (store.active_root / relative).exists()

    handle.record_reaped(os.getpid(), os.getpgrp())
    lease.__exit__(None, None, None)
    assert (store.active_root / relative).is_file()


def test_recovery_promotes_orphan_after_process_group_releases_view_lease(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    home, _ = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )

    handle = lease.__enter__()
    relative = Path("2026/07/rollout-orphan.jsonl")
    _rollout((home / "sessions").resolve() / relative, "thread-orphan", cwd=tmp_path)
    handle.record_spawn(12345, 12345)
    lease.view_lease.release()

    store.recover()

    assert not lease.view_path.exists()
    assert (store.active_root / relative).is_file()
    assert store.read_index(str(tmp_path))[0].session_id == "thread-orphan"


def test_fresh_rollout_is_promoted_durably_and_indexed_once(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-root"
    index_path = log_dir / "codex-session-index.json"
    store = CodexSessionStore(log_dir=log_dir, index_path=index_path)
    home, inert_targets = _generated_home(tmp_path)
    expected: bytes

    with store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    ) as handle:
        expected = _rollout(
            (home / "sessions").resolve() / "2026" / "07" / "rollout-new.jsonl",
            "thread-new",
        )
        handle.record_spawn(os.getpid(), os.getpgrp())
        handle.record_reaped(os.getpid(), os.getpgrp())

    promoted = log_dir / "codex-sessions" / "2026" / "07" / "rollout-new.jsonl"
    assert promoted.read_bytes() == expected
    assert (home / "sessions").resolve() == inert_targets["sessions"]
    assert (home / "archived_sessions").resolve() == inert_targets["archived_sessions"]
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    matching = [row for row in rows if row["session_id"] == "thread-new"]
    assert len(matching) == 1
    assert matching[0]["backend_name"] == "codex"
    assert matching[0]["cwd"] == str(tmp_path)
    assert matching[0]["canonical_store"] == "active"
    assert matching[0]["relative_path"] == "2026/07/rollout-new.jsonl"


@pytest.mark.parametrize(
    ("store_name", "public_name"),
    [
        pytest.param("codex-sessions", "sessions", id="active"),
        pytest.param("codex-archived-sessions", "archived_sessions", id="archived"),
    ],
)
def test_named_resume_hard_links_only_selected_rollout_into_matching_view(
    tmp_path: Path, store_name: str, public_name: str
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    canonical = log_dir / store_name / "2026" / "07" / "rollout-resume.jsonl"
    _rollout(canonical, "thread-resume")
    unrelated = log_dir / store_name / "2026" / "07" / "rollout-unrelated.jsonl"
    _rollout(unrelated, "thread-unrelated")
    home, inert_targets = _generated_home(tmp_path)
    canonical_identity = (canonical.stat().st_dev, canonical.stat().st_ino)

    with store.prepare_attempt(
        session_home=home,
        project_dir=tmp_path,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NamedResume("thread-resume"),
    ) as handle:
        resumed = (home / public_name).resolve() / "2026" / "07" / canonical.name
        assert resumed.is_file()
        assert (resumed.stat().st_dev, resumed.stat().st_ino) == canonical_identity
        assert not ((home / public_name).resolve() / "2026" / "07" / unrelated.name).exists()
        other_public = "archived_sessions" if public_name == "sessions" else "sessions"
        assert list((home / other_public).resolve().rglob("*.jsonl")) == []
        handle.record_spawn(os.getpid(), os.getpgrp())
        handle.record_reaped(os.getpid(), os.getpgrp())

    assert (canonical.stat().st_dev, canonical.stat().st_ino) == canonical_identity
    assert unrelated.is_file()
    assert (home / "sessions").resolve() == inert_targets["sessions"]
    assert (home / "archived_sessions").resolve() == inert_targets["archived_sessions"]
