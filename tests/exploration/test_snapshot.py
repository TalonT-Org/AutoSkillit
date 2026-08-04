from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

import autoskillit.exploration.snapshot as snapshot_module
from autoskillit.exploration.collectors import _bounded
from autoskillit.exploration.snapshot import (
    SnapshotCaptureLimits,
    SnapshotCaptureStatus,
    capture_repository_snapshot,
)

pytestmark = [
    pytest.mark.layer("exploration"),
    pytest.mark.feature("exploration"),
    pytest.mark.medium,
]

_COLLECTOR_MANIFEST_DIGEST = "sha256:collector-manifest"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _new_repository(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "snapshot@example.test")
    _git(root, "config", "user.name", "Snapshot Test")
    (root / ".gitignore").write_text("*.private\n")
    (root / "head.txt").write_text("head\n")
    (root / "staged.txt").write_text("before-index\n")
    (root / "tracked.txt").write_text("before-working-tree\n")
    (root / "missing.txt").write_text("will be removed\n")
    (root / "link.txt").symlink_to("tracked.txt")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "initial")
    return root


def _capture(root: Path):
    return capture_repository_snapshot(
        root,
        collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST,
    )


def test_snapshot_captures_all_repository_state_without_exposing_ignored_bytes(
    tmp_path: Path,
) -> None:
    root = _new_repository(tmp_path)
    baseline = _capture(root)
    assert baseline.status is SnapshotCaptureStatus.COMPLETE
    assert baseline.snapshot is not None
    (root / "tracked.txt").write_text("working-tree-bytes\n")
    (root / "staged.txt").write_text("index-bytes\n")
    _git(root, "add", "staged.txt")
    (root / "missing.txt").unlink()
    (root / "untracked.txt").write_text("untracked-bytes\n")
    ignored = root / "ignored.private"
    ignored.write_text("ignored-secret-bytes\n")
    (root / "tracked.txt").chmod(0o755)

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.COMPLETE
    assert result.snapshot is not None
    snapshot = result.snapshot
    assert snapshot.head_sha
    assert snapshot.index_digest.startswith("sha256:")
    assert snapshot.tree_digest.startswith("sha256:")
    assert snapshot.head_sha == baseline.snapshot.head_sha
    assert snapshot.index_digest != baseline.snapshot.index_digest
    assert dict(snapshot.tracked_records)["tracked.txt"].startswith("sha256:")
    assert dict(snapshot.tracked_records)["staged.txt"].startswith("sha256:")
    assert dict(snapshot.untracked_records)["untracked.txt"].startswith("sha256:")
    assert snapshot.ignored_records == (("ignored.private", ""),)
    assert snapshot.missing_records == (("missing.txt", "missing"),)
    assert ("tracked.txt", "755") in snapshot.mode_records
    assert snapshot.symlink_records == (("link.txt", "tracked.txt"),)

    ignored_digest = f"sha256:{hashlib.sha256(ignored.read_bytes()).hexdigest()}"
    assert ignored_digest not in snapshot.ignored_records
    assert "ignored-secret-bytes" not in repr(snapshot)

    ignored.write_text("rotated-ignored-secret\n")
    changed = _capture(root)

    assert changed.status is SnapshotCaptureStatus.COMPLETE
    assert changed.snapshot is not None
    assert changed.snapshot.ignored_records == snapshot.ignored_records
    assert changed.snapshot.tree_digest != snapshot.tree_digest
    assert changed.snapshot.pagination_identity != snapshot.pagination_identity


def test_snapshot_identity_shares_common_git_directory_between_worktrees(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)
    companion = tmp_path / "companion"
    _git(root, "worktree", "add", "-qb", "companion", str(companion))

    primary = _capture(root)
    secondary = _capture(companion)

    assert primary.status is SnapshotCaptureStatus.COMPLETE
    assert secondary.status is SnapshotCaptureStatus.COMPLETE
    assert primary.snapshot is not None
    assert secondary.snapshot is not None
    assert primary.snapshot.identity.repository == secondary.snapshot.identity.repository
    assert primary.snapshot.identity.common_git_dir == secondary.snapshot.identity.common_git_dir
    assert primary.snapshot.identity.worktree_path != secondary.snapshot.identity.worktree_path


def test_snapshot_publishes_atomic_stale_marker_when_start_and_end_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    capture_once = snapshot_module._capture_once
    calls = 0

    def mutate_between_captures(
        observed_root: Path, limits: SnapshotCaptureLimits
    ) -> snapshot_module.CapturedRepositoryState:
        nonlocal calls
        observed = capture_once(observed_root, limits)
        calls += 1
        if calls == 1:
            (root / "appeared-between-captures.txt").write_text("mutation")
        return observed

    monkeypatch.setattr(snapshot_module, "_capture_once", mutate_between_captures)

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.STALE
    assert result.snapshot is not None
    assert result.snapshot.stale
    assert result.snapshot.state == "stale"
    assert result.snapshot.tracked_records == ()
    assert result.snapshot.untracked_records == ()
    assert result.snapshot.ignored_records == ()
    assert result.start_identity != result.end_identity


def test_snapshot_publishes_atomic_terminal_marker_when_a_limit_truncates(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)

    result = capture_repository_snapshot(
        root,
        collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST,
        limits=SnapshotCaptureLimits(max_paths=1),
    )

    assert result.status is SnapshotCaptureStatus.TRUNCATED
    assert result.snapshot is not None
    assert result.snapshot.truncated
    assert result.snapshot.state == "truncated"
    assert result.snapshot.tree_digest == ""
    assert result.snapshot.tracked_records == ()
    assert result.snapshot.untracked_records == ()
    assert result.snapshot.ignored_records == ()


def test_snapshot_records_are_stably_ordered_and_snapshot_bound(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)
    for name in ("z.txt", "a.txt", "middle.txt"):
        (root / name).write_text(name)

    first = _capture(root)
    second = _capture(root)

    assert first.status is SnapshotCaptureStatus.COMPLETE
    assert second.status is SnapshotCaptureStatus.COMPLETE
    assert first.snapshot is not None
    assert second.snapshot is not None
    assert first.snapshot.untracked_records == second.snapshot.untracked_records
    assert tuple(path for path, _ in first.snapshot.untracked_records) == (
        "a.txt",
        "middle.txt",
        "z.txt",
    )
    assert first.snapshot.pagination_identity == second.snapshot.pagination_identity


def test_snapshot_treats_untracked_special_files_as_metadata_only(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)
    os.mkfifo(root / "unreadable.pipe")

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.COMPLETE
    assert result.snapshot is not None
    assert ("unreadable.pipe", "") in result.snapshot.untracked_records


def test_snapshot_fails_closed_when_a_hashed_file_is_swapped_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    replacement = root / "replacement.txt"
    replacement.write_text("replacement")
    original_open = _bounded.os.open
    swapped = False

    def swap_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == "tracked.txt" and dir_fd is not None and not swapped:
            os.replace(replacement, root / "tracked.txt")
            swapped = True
        return descriptor

    monkeypatch.setattr(_bounded.os, "open", swap_after_open)

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.FAILED
    assert result.snapshot is None
