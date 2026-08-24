from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.exploration.snapshot as snapshot_module
from autoskillit.core import (
    CompletenessReport,
    EvidencePage,
    ExplorationQuerySpec,
    RepositorySnapshot,
)
from autoskillit.exploration import SnapshotCaptureReason, SnapshotCaptureStatus
from autoskillit.exploration.collectors import _bounded
from autoskillit.exploration.pagination import pagination_identity
from autoskillit.exploration.snapshot import (
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    SnapshotCaptureLimits,
    SnapshotCaptureResult,
    StableArtifactCapture,
    capture_repository_snapshot,
    capture_stable_artifact,
    stable_artifact_matches,
)
from autoskillit.pipeline import ExplorationContext, OwnerBoundExplorationContextStore

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
) -> StableArtifactCapture:
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
    records = tuple(
        f"100644 {object_id} {stage}\t{path}\n"
        for stage, object_id in enumerate(object_ids, start=1)
    )
    _git_stdout(
        root,
        "update-index",
        "--index-info",
        input_bytes="".join(records).encode(),
    )
    return tuple(record.rstrip("\n") for record in records)


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
    assert re.fullmatch(r"[0-9a-f]{64}", captured.repository_identity_digest)
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

    assert raised.value.status is ArtifactCaptureStatus.UNSUPPORTED
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

    assert raised.value.status is ArtifactCaptureStatus.UNSUPPORTED


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

    assert raised.value.status is ArtifactCaptureStatus.STALE
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

    assert raised.value.status is ArtifactCaptureStatus.UNSUPPORTED
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
        observed_root: Path, limits: SnapshotCaptureLimits, *, deadline: float
    ) -> snapshot_module.CapturedRepositoryState:
        nonlocal calls
        observed = capture_once(observed_root, limits, deadline=deadline)
        calls += 1
        if calls == 1:
            (root / "appeared-between-captures.txt").write_text("mutation")
        return observed

    monkeypatch.setattr(snapshot_module, "_capture_once", mutate_between_captures)

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.STALE
    assert result.snapshot is not None
    assert result.validated_activation is None
    assert result.reason is SnapshotCaptureReason.IDENTITY_DRIFT
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
    assert result.reason is SnapshotCaptureReason.PATH_COUNT_EXCEEDED
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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation requires POSIX")
def test_snapshot_survives_a_real_entry_deleted_during_the_worktree_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    fifo_path = root / "vanishing.pipe"
    os.mkfifo(fifo_path)
    real_observe_path_mode = snapshot_module.observe_path_mode
    unlinked = False

    def unlink_first_then_delegate(path: Path):
        nonlocal unlinked
        if not unlinked and path == fifo_path:
            unlinked = True
            fifo_path.unlink()
        return real_observe_path_mode(path)

    monkeypatch.setattr(snapshot_module, "observe_path_mode", unlink_first_then_delegate)

    result = _capture(root)

    assert result.status is not SnapshotCaptureStatus.FAILED
    assert result.status in (SnapshotCaptureStatus.COMPLETE, SnapshotCaptureStatus.STALE)
    assert result.snapshot is not None
    # COMPLETE is itself the self-consistency proof: capture_repository_snapshot
    # only reaches COMPLETE when the start and end internal captures agree on
    # snapshot_identity (else it publishes STALE), so a COMPLETE result here
    # means both internal captures independently agreed on the FIFO's fate —
    # not merely "not FAILED", but a snapshot that never claims state it did
    # not actually observe.
    assert unlinked


def test_snapshot_walk_does_not_descend_into_collapsed_ignored_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    scratch = root / "scratch"
    scratch.mkdir()
    (scratch / "a.txt").write_text("a")
    (scratch / "b.txt").write_text("b")
    with (root / ".gitignore").open("a") as f:
        f.write("scratch/\n")

    real_observe_path_mode = snapshot_module.observe_path_mode
    observed_paths: list[Path] = []

    def record_and_delegate(path: Path):
        observed_paths.append(path)
        return real_observe_path_mode(path)

    monkeypatch.setattr(snapshot_module, "observe_path_mode", record_and_delegate)

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.COMPLETE
    # `scratch` itself is legitimately observed once per capture as git's own
    # collapsed ignored-directory entry (via _path_state) — the walk under
    # test here is _untracked_special_paths, which must not descend *into* it.
    assert not any(scratch in path.parents for path in observed_paths)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation requires POSIX")
def test_snapshot_omits_special_file_inside_collapsed_ignored_directory(tmp_path: Path) -> None:
    root = _new_repository(tmp_path)
    scratch = root / "scratch"
    scratch.mkdir()
    (scratch / "keep.txt").write_text("keep")
    os.mkfifo(scratch / "inside.pipe")
    os.mkfifo(root / "outside.pipe")
    with (root / ".gitignore").open("a") as f:
        f.write("scratch/\n")

    result = _capture(root)

    assert result.status is SnapshotCaptureStatus.COMPLETE
    assert result.snapshot is not None
    assert not any(path == "scratch/inside.pipe" for path, _ in result.snapshot.untracked_records)
    assert ("outside.pipe", "") in result.snapshot.untracked_records


def test_ignore_policy_bump_changes_the_published_digest_for_unchanged_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authority file's persisted snapshot_digest is only as trustworthy as the
    ignore policy it was computed under (pipeline/exploration_context_durable.py
    signs RepositorySnapshot.digest, which covers tree_digest and
    ignore_policy_digest — see _terminal_snapshot/_complete_snapshot). Prove the
    half of that contract this module owns: capturing identical repository state
    under two different DEFAULT_IGNORE_POLICY values must not silently collide on
    the same digest — a v1-era digest must not validate against a v2 capture. The
    other half — that this divergence actually degrades a live capability to the
    store's existing fail-closed ValueError — is pinned immediately below by
    test_ignore_policy_bump_rebind_against_v2_capture_fails_closed, and separately
    (for the tampered/missing signed-authority-file variant of the same failure,
    exercised through a mocked service) by
    test_tampered_or_missing_signed_snapshot_binding_fails_closed in
    tests/pipeline/test_exploration_context.py.
    """
    root = _new_repository(tmp_path)

    monkeypatch.setattr(
        snapshot_module, "DEFAULT_IGNORE_POLICY", "ignored-names-modes-collapsed-v1"
    )
    under_v1 = _capture(root)
    monkeypatch.setattr(
        snapshot_module, "DEFAULT_IGNORE_POLICY", "ignored-names-modes-collapsed-v2"
    )
    under_v2 = _capture(root)

    assert under_v1.status is SnapshotCaptureStatus.COMPLETE
    assert under_v2.status is SnapshotCaptureStatus.COMPLETE
    assert under_v1.snapshot is not None
    assert under_v2.snapshot is not None
    assert under_v1.snapshot.digest != under_v2.snapshot.digest
    assert under_v1.snapshot.ignore_policy_digest != under_v2.snapshot.ignore_policy_digest


def test_ignore_policy_bump_rebind_against_v2_capture_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-A7: a capability bound while DEFAULT_IGNORE_POLICY is v1 must fail closed —
    not silently re-validate — once the policy is bumped to v2 before the bound
    capability's next use.

    Complements test_ignore_policy_bump_changes_the_published_digest_for_unchanged_state
    above: that test proves the two policies compute distinct digests for identical
    repository state; this test proves OwnerBoundExplorationContextStore actually acts
    on that divergence — submit_for_capability degrades to the store's existing
    fail-closed ValueError (pipeline/exploration_context.py) rather than accepting a
    stale v1-era lease against a live v2 capture. A thin real-capture service adapter
    is used (not a mock) so the digest comparison exercises this module's own
    capture_repository_snapshot on both sides of the bump.
    """
    root = _new_repository(tmp_path)

    class _RealCaptureService:
        """Adapts capture_repository_snapshot to ExplorationServiceProtocol."""

        def capture_snapshot(self, root: Path) -> RepositorySnapshot:
            captured = _capture(root)
            assert captured.status is SnapshotCaptureStatus.COMPLETE
            assert captured.snapshot is not None
            return captured.snapshot

        def collect(self, query: ExplorationQuerySpec, *, root: Path) -> ExplorationContext:
            return ExplorationContext(
                query=query,
                snapshot=self.capture_snapshot(root),
                evidence=(),
                completeness=CompletenessReport(expected_collectors=(), reports=(), complete=True),
            )

        def page(
            self,
            context: ExplorationContext,
            *,
            page_size: int,
            cursor: object | None = None,
        ) -> EvidencePage:
            raise AssertionError(
                "page() must not be reached once the snapshot-digest check fails closed"
            )

    monkeypatch.setattr(
        snapshot_module, "DEFAULT_IGNORE_POLICY", "ignored-names-modes-collapsed-v1"
    )
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=root,
        service=_RealCaptureService(),
    )
    capability = store.bind_session_scoped(
        owner_id="uid:1000",
        session_id="session-a",
        cwd=root,
        repository_root=root,
        source_identity="bundled:definition-digest",
    )

    monkeypatch.setattr(
        snapshot_module, "DEFAULT_IGNORE_POLICY", "ignored-names-modes-collapsed-v2"
    )

    with pytest.raises(
        ValueError, match="repository snapshot changed since exploration authority issuance"
    ):
        store.submit_for_capability(
            capability=capability,
            query=ExplorationQuerySpec("needle"),
            page_size=10,
        )


def _new_repository_with_tracked_file_in_ignored_dir(tmp_path: Path, name: str = "repo") -> Path:
    """A ``vendor/`` directory that is ignored but not fully collapsed.

    ``vendor/keep.txt`` is force-added, defeating git's ``--directory`` collapse
    for ``ls-files --others --ignored --directory`` — the precondition that makes
    the ignored-byte budgets reachable at all (T-B3).
    """
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "snapshot@example.test")
    _git(root, "config", "user.name", "Snapshot Test")
    (root / ".gitignore").write_text("vendor/\n")
    vendor = root / "vendor"
    vendor.mkdir()
    (vendor / "keep.txt").write_text("kept tracked bytes\n")
    (vendor / "big.bin").write_bytes(b"\x00" * (3 * 1024 * 1024))
    _git(root, "add", ".gitignore")
    _git(root, "add", "-f", "vendor/keep.txt")
    _git(root, "commit", "-qm", "initial")
    return root


def test_ignored_bytes_are_not_charged_but_still_invalidate_the_fingerprint(
    tmp_path: Path,
) -> None:
    root = _new_repository_with_tracked_file_in_ignored_dir(tmp_path)
    big = root / "vendor" / "big.bin"
    big_size = big.stat().st_size
    limits = SnapshotCaptureLimits(max_file_bytes=big_size - 1, max_total_bytes=big_size - 1)

    result = capture_repository_snapshot(
        root, collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST, limits=limits
    )

    assert result.status is SnapshotCaptureStatus.COMPLETE
    assert result.snapshot is not None
    assert ("vendor/big.bin", "") in result.snapshot.ignored_records

    baseline_digest = result.snapshot.tree_digest
    big.write_bytes(b"\x01" * big_size)
    rewritten = capture_repository_snapshot(
        root, collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST, limits=limits
    )

    assert rewritten.status is SnapshotCaptureStatus.COMPLETE
    assert rewritten.snapshot is not None
    assert rewritten.snapshot.tree_digest != baseline_digest

    tracked_over_limit = capture_repository_snapshot(
        root,
        collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST,
        limits=SnapshotCaptureLimits(max_file_bytes=10, max_total_bytes=1_000_000),
    )

    assert tracked_over_limit.status is SnapshotCaptureStatus.TRUNCATED
    assert tracked_over_limit.reason is SnapshotCaptureReason.FILE_BYTES_EXCEEDED


def _trip_max_paths(root: Path, monkeypatch: pytest.MonkeyPatch) -> SnapshotCaptureLimits:
    _new_repository(root.parent, name=root.name)
    return SnapshotCaptureLimits(max_paths=1)


def _trip_max_file_bytes(root: Path, monkeypatch: pytest.MonkeyPatch) -> SnapshotCaptureLimits:
    repo = _new_repository(root.parent, name=root.name)
    (repo / "big.txt").write_text("x" * 64)
    _git(repo, "add", "big.txt")
    _git(repo, "commit", "-qm", "big file")
    return SnapshotCaptureLimits(max_file_bytes=32)


def _trip_max_total_bytes(root: Path, monkeypatch: pytest.MonkeyPatch) -> SnapshotCaptureLimits:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "snapshot@example.test")
    _git(root, "config", "user.name", "Snapshot Test")
    for name in ("t1.txt", "t2.txt", "t3.txt"):
        (root / name).write_text("x" * 20)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "bulk files")
    return SnapshotCaptureLimits(max_file_bytes=1000, max_total_bytes=30)


def _trip_git_timeout_seconds(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> SnapshotCaptureLimits:
    _new_repository(root.parent, name=root.name)

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 1))  # type: ignore[arg-type]

    monkeypatch.setattr(snapshot_module.subprocess, "run", raise_timeout)
    return SnapshotCaptureLimits(git_timeout_seconds=1)


def _force_capture_deadline_overrun(monkeypatch: pytest.MonkeyPatch) -> None:
    real_capture_once = snapshot_module._capture_once

    def jump_then_delegate(
        root_arg: Path, limits: SnapshotCaptureLimits, *, deadline: float
    ) -> snapshot_module.CapturedRepositoryState:
        monkeypatch.setattr(snapshot_module.time, "monotonic", lambda: deadline + 1)
        return real_capture_once(root_arg, limits, deadline=deadline)

    monkeypatch.setattr(snapshot_module, "_capture_once", jump_then_delegate)


def _trip_capture_deadline_seconds(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> SnapshotCaptureLimits:
    _new_repository(root.parent, name=root.name)
    _force_capture_deadline_overrun(monkeypatch)
    return SnapshotCaptureLimits(capture_deadline_seconds=60)


_LIMIT_TRIP_BUILDERS: Mapping[str, Callable[[Path, pytest.MonkeyPatch], SnapshotCaptureLimits]] = {
    "max_paths": _trip_max_paths,
    "max_file_bytes": _trip_max_file_bytes,
    "max_total_bytes": _trip_max_total_bytes,
    "git_timeout_seconds": _trip_git_timeout_seconds,
    "capture_deadline_seconds": _trip_capture_deadline_seconds,
}

_EXPECTED_REASON_BY_LIMIT_FIELD: Mapping[str, SnapshotCaptureReason] = {
    "max_paths": SnapshotCaptureReason.PATH_COUNT_EXCEEDED,
    "max_file_bytes": SnapshotCaptureReason.FILE_BYTES_EXCEEDED,
    "max_total_bytes": SnapshotCaptureReason.TOTAL_BYTES_EXCEEDED,
    "git_timeout_seconds": SnapshotCaptureReason.GIT_TIMEOUT,
    "capture_deadline_seconds": SnapshotCaptureReason.CAPTURE_DEADLINE_EXCEEDED,
}

_EXPECTED_STATUS_BY_LIMIT_FIELD: Mapping[str, SnapshotCaptureStatus] = {
    "max_paths": SnapshotCaptureStatus.TRUNCATED,
    "max_file_bytes": SnapshotCaptureStatus.TRUNCATED,
    "max_total_bytes": SnapshotCaptureStatus.TRUNCATED,
    "git_timeout_seconds": SnapshotCaptureStatus.FAILED,
    "capture_deadline_seconds": SnapshotCaptureStatus.FAILED,
}


@pytest.mark.parametrize(
    "field",
    sorted(field.name for field in dataclasses.fields(SnapshotCaptureLimits)),
    ids=lambda name: name,
)
def test_every_bounding_limit_field_has_a_trip_builder(field: str) -> None:
    assert field in _LIMIT_TRIP_BUILDERS, (
        f"{field} has no trip builder in _LIMIT_TRIP_BUILDERS — a new "
        "SnapshotCaptureLimits field must add one so its enforcement is exercised."
    )


@pytest.mark.parametrize(
    "field",
    sorted(field.name for field in dataclasses.fields(SnapshotCaptureLimits)),
    ids=lambda name: name,
)
def test_each_bounding_limit_field_trips_its_own_reason(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _LIMIT_TRIP_BUILDERS[field]
    root = tmp_path / field
    limits = builder(root, monkeypatch)

    result = capture_repository_snapshot(
        root, collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST, limits=limits
    )

    assert result.reason is _EXPECTED_REASON_BY_LIMIT_FIELD[field]
    assert result.status is _EXPECTED_STATUS_BY_LIMIT_FIELD[field]


@pytest.mark.parametrize("status", list(SnapshotCaptureStatus), ids=lambda status: status.value)
def test_snapshot_capture_result_requires_reason_iff_not_complete(
    status: SnapshotCaptureStatus,
) -> None:
    if status is SnapshotCaptureStatus.COMPLETE:
        with pytest.raises(ValueError, match="cannot expose a failure reason"):
            SnapshotCaptureResult(
                status=status,
                snapshot=None,
                reason=SnapshotCaptureReason.MANIFEST_DIGEST_EMPTY,
            )
    else:
        with pytest.raises(ValueError, match="requires a failure reason"):
            SnapshotCaptureResult(status=status, snapshot=None, reason=None)


def test_snapshot_capture_deadline_overrun_fails_with_a_named_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _new_repository(tmp_path)
    _force_capture_deadline_overrun(monkeypatch)

    result = capture_repository_snapshot(
        root,
        collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST,
        limits=SnapshotCaptureLimits(capture_deadline_seconds=60),
    )

    assert result.status is SnapshotCaptureStatus.FAILED
    assert result.reason is SnapshotCaptureReason.CAPTURE_DEADLINE_EXCEEDED


def _raise_runtime_error(*args: object, **kwargs: object) -> None:
    raise RuntimeError("simulated failure")


def _setup_identity_resolution_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _new_repository(tmp_path, name="identity-failure")
    monkeypatch.setattr(snapshot_module, "resolve_repository_identity", _raise_runtime_error)
    return root


def _setup_profile_activation_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _new_repository(tmp_path, name="activation-failure")
    monkeypatch.setattr(snapshot_module, "activate_repository_profiles", _raise_runtime_error)
    return root


class _FailedGitProcess:
    returncode = 1
    stdout = b""
    stderr = b"simulated git failure"


def _setup_git_command_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _new_repository(tmp_path, name="git-command-failure")
    monkeypatch.setattr(
        snapshot_module.subprocess, "run", lambda *args, **kwargs: _FailedGitProcess()
    )
    return root


def _setup_git_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _new_repository(tmp_path, name="git-timeout")

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 1))  # type: ignore[arg-type]

    monkeypatch.setattr(snapshot_module.subprocess, "run", raise_timeout)
    return root


def _setup_root_worktree_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _new_repository(tmp_path, name="worktree-mismatch")
    nested = root / "subdir"
    nested.mkdir()
    return nested


_FAILED_CAUSE_SCENARIOS: tuple[
    tuple[str, Callable[[Path, pytest.MonkeyPatch], Path], SnapshotCaptureReason], ...
] = (
    (
        "identity_resolution",
        _setup_identity_resolution_failure,
        SnapshotCaptureReason.IDENTITY_UNRESOLVED,
    ),
    (
        "profile_activation",
        _setup_profile_activation_failure,
        SnapshotCaptureReason.PROFILE_ACTIVATION_FAILED,
    ),
    (
        "git_command_failure",
        _setup_git_command_failure,
        SnapshotCaptureReason.GIT_COMMAND_FAILED,
    ),
    ("git_timeout", _setup_git_timeout, SnapshotCaptureReason.GIT_TIMEOUT),
    (
        "root_worktree_mismatch",
        _setup_root_worktree_mismatch,
        SnapshotCaptureReason.ROOT_NOT_WORKTREE,
    ),
)


@pytest.mark.parametrize(
    ("scenario", "setup", "expected_reason"),
    _FAILED_CAUSE_SCENARIOS,
    ids=[case[0] for case in _FAILED_CAUSE_SCENARIOS],
)
def test_snapshot_failed_causes_are_named_distinctly(
    scenario: str,
    setup: Callable[[Path, pytest.MonkeyPatch], Path],
    expected_reason: SnapshotCaptureReason,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = setup(tmp_path, monkeypatch)

    result = capture_repository_snapshot(
        root, collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST
    )

    assert result.status is SnapshotCaptureStatus.FAILED
    assert result.reason is expected_reason


def test_snapshot_failed_cause_scenarios_are_pairwise_distinct() -> None:
    reasons = [reason for _scenario, _setup, reason in _FAILED_CAUSE_SCENARIOS]
    assert len(reasons) == len(set(reasons))


@pytest.mark.xfail(
    strict=True,
    reason="max_paths counts collapsed ignored entries toward the cap; tracked in #4778",
)
def test_max_paths_does_not_count_collapsed_ignored_entries(tmp_path: Path) -> None:
    # Only 2 non-ignored paths exist (.gitignore, vendor/keep.txt); the 9 ignored
    # files below (big.bin plus 8 more) currently count toward max_paths too,
    # tripping PATH_COUNT_EXCEEDED at a cap that should comfortably admit them.
    root = _new_repository_with_tracked_file_in_ignored_dir(tmp_path)
    vendor = root / "vendor"
    for index in range(8):
        (vendor / f"ignored-{index}.bin").write_bytes(b"x")

    result = capture_repository_snapshot(
        root,
        collector_manifest_digest=_COLLECTOR_MANIFEST_DIGEST,
        limits=SnapshotCaptureLimits(max_paths=5),
    )

    assert result.status is SnapshotCaptureStatus.COMPLETE
