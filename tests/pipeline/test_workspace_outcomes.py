"""Tests for durable workspace outcome accounting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactLease,
    CommitFailureClass,
    WorkspaceOutcomeKind,
    WorkspaceOutcomeRecord,
)
from autoskillit.pipeline import DefaultWorkspaceOutcomeLedger
from autoskillit.pipeline.workspace_outcomes import _ledger as outcome_module
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.medium]


def _record(
    workspace: Path,
    recorded_at: str,
    *,
    kind: WorkspaceOutcomeKind = WorkspaceOutcomeKind.TEST_RUN,
    succeeded: bool = True,
    commit_sha: str | None = None,
    failure_class: CommitFailureClass | None = None,
) -> WorkspaceOutcomeRecord:
    return WorkspaceOutcomeRecord(
        workspace=str(workspace),
        recorded_at=recorded_at,
        kind=kind,
        succeeded=succeeded,
        commit_sha=commit_sha,
        failure_class=failure_class,
    )


def test_record_contract_rejects_ambiguous_commit_outcomes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires commit_sha"):
        _record(
            tmp_path,
            "2026-09-16T10:00:00+00:00",
            kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
            succeeded=True,
        )
    with pytest.raises(ValueError, match="requires failure_class"):
        _record(
            tmp_path,
            "2026-09-16T10:00:00+00:00",
            kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
            succeeded=False,
        )
    with pytest.raises(ValueError, match="cannot have commit_sha"):
        _record(
            tmp_path,
            "2026-09-16T10:00:00+00:00",
            kind=WorkspaceOutcomeKind.COMMIT_ATTEMPT,
            succeeded=False,
            commit_sha="abc123",
            failure_class=CommitFailureClass.GIT_COMMIT_FAILED,
        )


def test_read_uses_inclusive_utc_window(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    first = _record(workspace, "2026-09-16T10:00:00+00:00")
    second = _record(workspace, "2026-09-16T03:00:01-07:00", succeeded=False)
    ledger.record(first)
    ledger.record(second)

    assert ledger.read(
        str(workspace),
        since="2026-09-16T10:00:00Z",
        until="2026-09-16T10:00:01+00:00",
    ) == [first, second]


@pytest.mark.parametrize(
    "content",
    [
        "not-json\n",
        '{"discarded_through":null}\n{"workspace":',
        '{"wrong_header":null}\n',
    ],
)
def test_read_fails_closed_for_corrupt_or_incomplete_shard(
    tmp_path: Path,
    content: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    _, shard_path, lock_path = ledger._paths(str(workspace))
    lock_path.parent.mkdir(parents=True)
    lock_path.touch()
    shard_path.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="shard is unreadable"):
        ledger.read(
            str(workspace),
            since="2026-09-16T00:00:00+00:00",
            until="2026-09-17T00:00:00+00:00",
        )


def test_compaction_records_discarded_boundary_and_fails_closed_before_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outcome_module, "MAX_RETAINED_RECORDS", 2)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    records = [_record(workspace, f"2026-09-16T10:00:0{offset}+00:00") for offset in range(3)]
    for record in records:
        ledger.record(record)

    _, shard_path, _ = ledger._paths(str(workspace))
    rows = [json.loads(line) for line in shard_path.read_text().splitlines()]
    assert rows[0] == {"discarded_through": records[0].recorded_at}
    assert len(rows[1:]) == 2
    with pytest.raises(RuntimeError, match="evidence is incomplete"):
        ledger.read(
            str(workspace),
            since=records[0].recorded_at,
            until=records[-1].recorded_at,
        )
    assert (
        ledger.read(
            str(workspace),
            since=records[1].recorded_at,
            until=records[-1].recorded_at,
        )
        == records[1:]
    )


def test_compaction_enforces_shard_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _record(workspace, "2026-09-16T10:00:00+00:00")
    second = _record(workspace, "2026-09-16T10:00:01+00:00")
    one_retained = outcome_module._render_shard(
        first.recorded_at,
        [outcome_module._encode_row(outcome_module._record_payload(second))],
    )
    monkeypatch.setattr(outcome_module, "MAX_SHARD_BYTES", len(one_retained.encode()))
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")

    ledger.record(first)
    ledger.record(second)

    _, shard_path, _ = ledger._paths(str(workspace))
    rows = [json.loads(line) for line in shard_path.read_text().splitlines()]
    assert shard_path.stat().st_size <= outcome_module.MAX_SHARD_BYTES
    assert rows[0] == {"discarded_through": first.recorded_at}
    assert [row["recorded_at"] for row in rows[1:]] == [second.recorded_at]


def test_read_fails_closed_when_shard_becomes_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    record = _record(workspace, "2026-09-16T10:00:00+00:00")
    ledger.record(record)
    _, shard_path, _ = ledger._paths(str(workspace))
    real_read_bytes = Path.read_bytes

    def fail_shard_read(path: Path) -> bytes:
        if path == shard_path:
            raise PermissionError("unreadable")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_shard_read)

    with pytest.raises(RuntimeError, match="shard is unreadable"):
        ledger.read(
            str(workspace),
            since=record.recorded_at,
            until=record.recorded_at,
        )


def test_realpath_shard_is_symmetric_for_symlink_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    record = _record(alias, "2026-09-16T10:00:00+00:00")

    ledger.record(record)

    assert ledger._paths(str(alias))[1:] == ledger._paths(str(workspace))[1:]
    assert ledger.read(
        str(workspace),
        since="2026-09-16T00:00:00+00:00",
        until="2026-09-17T00:00:00+00:00",
    ) == [_record(workspace, "2026-09-16T10:00:00+00:00")]


def test_machine_local_detector_reports_vanished_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger_root = tmp_path / "ledger"
    ledger = DefaultWorkspaceOutcomeLedger(ledger_root)
    ledger.record(_record(workspace, "2026-09-16T10:00:00+00:00"))
    _, shard_path, _ = ledger._paths(str(workspace))

    workspace.rmdir()

    assert outcome_module.find_stale_workspace_outcome_shards(ledger_root) == (shard_path,)


def test_record_does_not_acknowledge_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    real_fsync = os.fsync
    calls = 0

    def fail_first_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        ledger.record(_record(workspace, "2026-09-16T10:00:00+00:00"))


def test_stable_lease_blocks_writer_until_barrier_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outcome_module, "ARTIFACT_LEASE_TIMEOUT_SECONDS", 1.0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "ledger")
    record = _record(workspace, "2026-09-16T10:00:00+00:00")
    _, _, lock_path = ledger._paths(str(workspace))
    # Use a Barrier so the writer reaches the lock acquisition before the test
    # thread proceeds, rather than polling acquisition_attempted.wait() with a
    # tight margin that can flake under xdist load or slow CI.
    barrier = threading.Barrier(2, timeout=10.0)
    acquire_exclusive = ArtifactLease.acquire_exclusive

    def acquire_after_barrier(lock_path: Path, *, timeout: float) -> ArtifactLease:
        # Block until the writer thread has confirmed it is waiting on the
        # lock and the test thread has finished asserting that no progress
        # was made; the writer side will then wait for the test thread to
        # release the shared lease.
        barrier.wait()
        return acquire_exclusive(lock_path, timeout=timeout)

    monkeypatch.setattr(ArtifactLease, "acquire_exclusive", acquire_after_barrier)

    lease = ArtifactLease.acquire_shared(lock_path, timeout=1.0)
    inode = lock_path.stat().st_ino
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ledger.record, record)
            # Wait for the writer to reach the patched acquire_exclusive;
            # release the writer once we have confirmed it has not finished.
            barrier.wait()
            assert not future.done()
            lease.close()
            future.result(timeout=2)
    finally:
        lease.close()
    assert lock_path.stat().st_ino == inode


def test_record_from_child_process_is_visible_to_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger_root = tmp_path / "ledger"
    script = """
import os
from pathlib import Path
from autoskillit.core import WorkspaceOutcomeKind, WorkspaceOutcomeRecord
from autoskillit.pipeline import DefaultWorkspaceOutcomeLedger

DefaultWorkspaceOutcomeLedger(Path(os.environ["LEDGER_ROOT"])).record(
    WorkspaceOutcomeRecord(
        workspace=os.environ["WORKSPACE"],
        recorded_at="2026-09-16T10:00:00+00:00",
        kind=WorkspaceOutcomeKind.TEST_RUN,
        succeeded=True,
    )
)
"""
    env = production_interpreter_env()
    env.update(LEDGER_ROOT=str(ledger_root), WORKSPACE=str(workspace))
    subprocess.run([sys.executable, "-c", script], check=True, env=env)

    assert DefaultWorkspaceOutcomeLedger(ledger_root).read(
        str(workspace),
        since="2026-09-16T10:00:00+00:00",
        until="2026-09-16T10:00:00+00:00",
    ) == [_record(workspace, "2026-09-16T10:00:00+00:00")]
