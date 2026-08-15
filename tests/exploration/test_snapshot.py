from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.exploration.snapshot as snapshot_module
from autoskillit.exploration.collectors import _bounded
from autoskillit.exploration.pagination import pagination_identity
from autoskillit.exploration.snapshot import (
    ArtifactCaptureError,
    SnapshotCaptureLimits,
    SnapshotCaptureStatus,
    capture_repository_snapshot,
    capture_stable_artifact,
    stable_artifact_matches,
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


def _capture_artifact(
    root: Path,
    artifact_path: str,
    *,
    max_attempts: int = 3,
    max_bytes: int = 1_000_000,
):
    return capture_stable_artifact(
        root,
        artifact_path,
        deadline=time.monotonic() + 5,
        max_attempts=max_attempts,
        max_bytes=max_bytes,
    )


def _git_stdout(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        input=input_bytes,
    ).stdout


def _set_unmerged_index(root: Path, path: str) -> tuple[str, ...]:
    object_ids = tuple(
        _git_stdout(root, "hash-object", "-w", "--stdin", input_bytes=content).decode().strip()
        for content in (b"base\n", b"ours\n", b"theirs\n")
    )
    _git(root, "rm", "--cached", "-q", "-f", "--", path)
    records = "".join(
        f"100644 {object_id} {stage}\t{path}\n"
        for stage, object_id in enumerate(object_ids, start=1)
    )
    _git_stdout(root, "update-index", "--index-info", input_bytes=records.encode())
    return tuple(
        record.decode()
        for record in _git_stdout(root, "ls-files", "--stage", "-z", "--", path).split(b"\0")
        if record
    )


def test_pagination_digest_preserves_golden_canonical_bytes() -> None:
    assert (
        pagination_identity(
            query=" Example  Query ",
            ordered_item_identities=("item-a", "item-b"),
            snapshot_identity="snapshot-a",
            profile_identity="profile-a",
            schema_identity="schema-a",
            collector_manifest_digest="manifest-a",
        )
        == "sha256:63bccbccdce27c7c1eb439e0523a77fcdb1fb16da823032176f8d940073e4db9"
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
    assert result.validated_activation is not None
    snapshot = result.snapshot
    activation = result.validated_activation
    assert snapshot.identity == activation.identity.repository_identity
    assert snapshot.profile_activation_digest == activation.activation_digest
    assert snapshot.profile_versions == activation.profile_versions
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


@pytest.mark.parametrize(
    ("state", "artifact_path", "expected_content"),
    [
        ("tracked", "tracked.txt", b"before-working-tree\n"),
        ("dirty", "tracked.txt", b"dirty-current-bytes\n"),
        ("staged", "staged.txt", b"staged-current-bytes\n"),
        ("untracked", "untracked.txt", b"untracked-current-bytes\n"),
        ("unmerged", "tracked.txt", b"unmerged-current-bytes\n"),
    ],
)
def test_stable_artifact_capture_returns_current_worktree_bytes(
    tmp_path: Path,
    state: str,
    artifact_path: str,
    expected_content: bytes,
) -> None:
    root = _new_repository(tmp_path)
    path = root / artifact_path
    expected_index_records: tuple[str, ...] | None = None
    if state in {"dirty", "staged", "untracked", "unmerged"}:
        path.write_bytes(expected_content)
    if state == "staged":
        _git(root, "add", "--", artifact_path)
    elif state == "unmerged":
        expected_index_records = _set_unmerged_index(root, artifact_path)

    captured = _capture_artifact(root, artifact_path)

    assert captured.repository_root == root.resolve()
    assert captured.artifact_path == artifact_path
    assert captured.content == expected_content
    assert captured.size == len(expected_content)
    assert captured.content_digest == (f"sha256:{hashlib.sha256(expected_content).hexdigest()}")
    assert captured.repository_identity_digest.startswith("sha256:")
    assert captured.revision
    assert captured.snapshot_digest.startswith("sha256:")
    if state == "untracked":
        assert captured.index_records == ()
    if expected_index_records is not None:
        assert captured.index_records == expected_index_records


def test_stable_artifact_digest_binds_mode_and_path_specific_index_records(
    tmp_path: Path,
) -> None:
    root = _new_repository(tmp_path)
    artifact = root / "tracked.txt"
    artifact.write_bytes(b"constant-worktree-bytes\n")
    baseline = _capture_artifact(root, artifact.name)

    artifact.chmod(0o755)
    changed_mode = _capture_artifact(root, artifact.name)
    artifact.chmod(0o644)
    staged_object = (
        _git_stdout(
            root,
            "hash-object",
            "-w",
            "--stdin",
            input_bytes=b"different-index-bytes\n",
        )
        .decode()
        .strip()
    )
    _git(root, "update-index", "--cacheinfo", "100644", staged_object, artifact.name)
    changed_index = _capture_artifact(root, artifact.name)

    assert baseline.content == changed_mode.content == changed_index.content
    assert baseline.snapshot_digest != changed_mode.snapshot_digest
    assert baseline.index_records != changed_index.index_records
    assert baseline.snapshot_digest != changed_index.snapshot_digest
    assert not stable_artifact_matches(baseline, changed_mode)
    assert not stable_artifact_matches(baseline, changed_index)


def test_stable_artifact_match_ignores_unrelated_repository_edit(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)
    start = _capture_artifact(root, "tracked.txt")
    (root / "head.txt").write_text("unrelated edit\n")
    _git(root, "add", "--", "head.txt")

    current = _capture_artifact(root, "tracked.txt")

    assert stable_artifact_matches(start, current)
    assert start.snapshot_digest == current.snapshot_digest


def test_stable_artifact_match_rejects_head_or_authorized_index_change(
    tmp_path: Path,
) -> None:
    root = _new_repository(tmp_path)
    artifact = root / "tracked.txt"
    start = _capture_artifact(root, artifact.name)
    _git(root, "commit", "--allow-empty", "-qm", "move head")
    changed_head = _capture_artifact(root, artifact.name)
    staged_object = (
        _git_stdout(
            root,
            "hash-object",
            "-w",
            "--stdin",
            input_bytes=b"index-only change\n",
        )
        .decode()
        .strip()
    )
    _git(root, "update-index", "--cacheinfo", "100644", staged_object, artifact.name)
    changed_index = _capture_artifact(root, artifact.name)

    assert not stable_artifact_matches(start, changed_head)
    assert not stable_artifact_matches(changed_head, changed_index)


@pytest.mark.parametrize(
    "artifact_path",
    ["/absolute.txt", r"nested\artifact.txt", "bad\0path", "../outside.txt", ".git/config"],
)
def test_stable_artifact_capture_rejects_unauthorized_path_forms(
    tmp_path: Path,
    artifact_path: str,
) -> None:
    root = _new_repository(tmp_path)

    with pytest.raises(ArtifactCaptureError) as raised:
        _capture_artifact(root, artifact_path)

    assert raised.value.status == "unsupported"
    assert raised.value.stop_reason == "invalid_artifact_path"


@pytest.mark.parametrize("kind", ["symlink-component", "special-file"])
def test_stable_artifact_capture_rejects_unsafe_artifacts(
    tmp_path: Path,
    kind: str,
) -> None:
    root = _new_repository(tmp_path)
    if kind == "symlink-component":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        (root / "linked").symlink_to(outside, target_is_directory=True)
        artifact_path = "linked/secret.txt"
    else:
        os.mkfifo(root / "special.pipe")
        artifact_path = "special.pipe"

    with pytest.raises(ArtifactCaptureError) as raised:
        _capture_artifact(root, artifact_path)

    assert raised.value.status == "unsupported"


def test_stable_artifact_capture_stops_after_bounded_instability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    calls = 0

    def reject_every_mutating_read(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        raise _bounded.CollectorMutationError("changed while reading")

    monkeypatch.setattr(
        snapshot_module,
        "read_stable_contained_file",
        reject_every_mutating_read,
    )

    with pytest.raises(ArtifactCaptureError) as raised:
        _capture_artifact(root, "tracked.txt", max_attempts=2)

    assert raised.value.status == "stale"
    assert raised.value.stop_reason
    assert calls == 2


def test_stable_artifact_capture_honors_absolute_deadline(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)

    with pytest.raises(ArtifactCaptureError) as raised:
        capture_stable_artifact(
            root,
            "tracked.txt",
            deadline=time.monotonic() - 1,
            max_attempts=1,
        )

    assert raised.value.status == "unsupported"
    assert raised.value.stop_reason == "deadline_exceeded"


def test_stable_artifact_capture_maps_nofollow_and_overflow_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    with pytest.raises(ArtifactCaptureError) as overflow:
        _capture_artifact(root, "tracked.txt", max_bytes=1)
    assert overflow.value.stop_reason == "artifact_too_large"

    monkeypatch.setattr(_bounded, "_SUPPORTS_NOFOLLOW_DIRECTORY_OPEN", False)
    with pytest.raises(ArtifactCaptureError) as nofollow:
        _capture_artifact(root, "tracked.txt")
    assert nofollow.value.stop_reason == "no_follow_unsupported"


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
    assert result.validated_activation is None
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
    assert result.validated_activation is None
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
    assert result.validated_activation is None


def test_snapshot_capture_rejects_profile_activation_toctou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    activate = snapshot_module.activate_repository_profiles
    activations = 0

    def change_second_activation(*args: object, **kwargs: object):
        nonlocal activations
        activation = activate(*args, **kwargs)
        activations += 1
        if activations == 2:
            return replace(activation, activation_digest="sha256:changed-activation")
        return activation

    monkeypatch.setattr(snapshot_module, "activate_repository_profiles", change_second_activation)

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.STALE
    assert result.snapshot is not None
    assert result.snapshot.stale
    assert result.validated_activation is None


def test_complete_capture_result_requires_consistent_validated_activation(
    tmp_path: Path,
) -> None:
    result = _capture(_new_repository(tmp_path))
    assert result.snapshot is not None
    assert result.validated_activation is not None

    with pytest.raises(ValueError, match="requires a validated activation"):
        replace(result, validated_activation=None)
    with pytest.raises(ValueError, match="repository identities must match"):
        replace(
            result,
            snapshot=replace(
                result.snapshot,
                identity=replace(result.snapshot.identity, revision="different-revision"),
            ),
        )
    with pytest.raises(ValueError, match="activation digests must match"):
        replace(
            result,
            snapshot=replace(
                result.snapshot,
                profile_activation_digest="sha256:different-activation",
            ),
        )
    with pytest.raises(ValueError, match="profile versions must match"):
        replace(
            result,
            snapshot=replace(result.snapshot, profile_versions=(("different", "1"),)),
        )


def test_terminal_capture_result_rejects_validated_activation(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)
    complete = _capture(root)
    assert complete.validated_activation is not None
    terminal = capture_repository_snapshot(
        root,
        collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST,
        limits=SnapshotCaptureLimits(max_paths=1),
    )

    with pytest.raises(ValueError, match="non-complete"):
        replace(terminal, validated_activation=complete.validated_activation)
