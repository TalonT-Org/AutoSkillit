"""Session-registry conversation and invocation identity invariants."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

import autoskillit.core.runtime.session_registry as subject
from autoskillit.core.runtime.session_registry import (
    bind_session_owner,
    bridge_claude_session_id,
    claim_launch_for_session,
    read_registry,
    registry_path,
    release_session_claim,
    write_registry_entry,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]

_CURRENT = (700, "test-boot", 11)
_OTHER = (701, "test-boot", 12)
_CHILD = (702, "test-boot", 13)


def _claim_in_child(project_dir: str, start: Any, finish: Any, outcomes: Any) -> None:
    """Attempt one real, independently locked claim after a bounded rendezvous."""
    start.wait(timeout=5)
    try:
        claim_launch_for_session(
            Path(project_dir),
            claude_session_id="shared-session",
            session_type="cook",
            recipe_name=None,
        )
    except ValueError:
        outcomes.put("refused")
    else:
        outcomes.put("claimed")
    finish.wait(timeout=5)


def _deterministic_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_current_identity", lambda: _CURRENT)


def test_write_refuses_second_launch_claiming_same_session_and_preserves_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    write_registry_entry(tmp_path, "first", "cook", None, claude_session_id="S-UUID")
    path = registry_path(tmp_path)
    before = path.read_bytes()

    with pytest.raises(
        ValueError,
        match=(
            "Session 'S-UUID' is already claimed by launch 'first'; "
            "cannot assign it to launch 'second'"
        ),
    ):
        write_registry_entry(tmp_path, "second", "order", None, claude_session_id="S-UUID")

    assert path.read_bytes() == before
    assert [
        launch_id
        for launch_id, row in read_registry(tmp_path).items()
        if row["claude_session_id"] == "S-UUID"
    ] == ["first"]


def test_same_row_retry_and_bridge_preserve_conversation_and_claimant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    write_registry_entry(tmp_path, "launch", "cook", None, claude_session_id="S-UUID")

    bridge_claude_session_id(tmp_path, "launch", "S-UUID")
    write_registry_entry(tmp_path, "launch", "cook", "recipe")

    row = read_registry(tmp_path)["launch"]
    assert row["claude_session_id"] == "S-UUID"
    assert (
        row["claimant_pid"],
        row["claimant_boot_id"],
        row["claimant_starttime_ticks"],
    ) == _CURRENT


def test_same_row_retarget_is_refused_before_foreign_claim_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    write_registry_entry(tmp_path, "launch", "cook", None, claude_session_id="old-session")
    path = registry_path(tmp_path)
    before = path.read_bytes()

    with pytest.raises(
        ValueError,
        match=(
            "Launch 'launch' is already bound to session 'old-session'; cannot bind 'new-session'"
        ),
    ):
        bridge_claude_session_id(tmp_path, "launch", "new-session")

    assert path.read_bytes() == before


def test_claim_reports_legacy_multiple_session_owners_without_rewriting(
    tmp_path: Path,
) -> None:
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"second":{"claude_session_id":"S-UUID"},"first":{"claude_session_id":"S-UUID"}}',
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(
        ValueError,
        match=r"Session 'S-UUID' is claimed by multiple launches: \['first', 'second'\]",
    ):
        claim_launch_for_session(
            tmp_path,
            claude_session_id="S-UUID",
            session_type="cook",
            recipe_name=None,
        )

    assert path.read_bytes() == before


def test_mutation_refuses_malformed_registry_without_rewriting(tmp_path: Path) -> None:
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="session registry is malformed"):
        write_registry_entry(tmp_path, "launch", "cook", None)

    assert path.read_bytes() == before


def test_claim_and_release_use_the_existing_exclusive_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    operations: list[int] = []
    monkeypatch.setattr(
        subject.fcntl,
        "flock",
        lambda _lock_file, operation: operations.append(operation),
    )

    launch_id = claim_launch_for_session(
        tmp_path,
        claude_session_id="S-UUID",
        session_type="cook",
        recipe_name=None,
    )
    assert release_session_claim(tmp_path, launch_id)

    assert operations == [fcntl.LOCK_EX | fcntl.LOCK_NB, fcntl.LOCK_EX | fcntl.LOCK_NB]


@pytest.mark.parametrize("liveness", [True, None])
def test_claim_refuses_live_or_unknown_existing_claimant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, liveness: bool | None
) -> None:
    _deterministic_identity(monkeypatch)
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"launch":{"claude_session_id":"S-UUID","claimant_pid":701,'
        '"claimant_boot_id":"test-boot","claimant_starttime_ticks":12}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(subject, "owner_liveness", lambda *_args: liveness)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="launch is reserved by a live or unavailable claimant"):
        claim_launch_for_session(
            tmp_path,
            claude_session_id="S-UUID",
            session_type="cook",
            recipe_name=None,
        )

    assert path.read_bytes() == before


def test_claim_replaces_affirmatively_dead_claimant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"launch":{"claude_session_id":"S-UUID","claimant_pid":701,'
        '"claimant_boot_id":"test-boot","claimant_starttime_ticks":12}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(subject, "owner_liveness", lambda *_args: False)

    assert (
        claim_launch_for_session(
            tmp_path,
            claude_session_id="S-UUID",
            session_type="cook",
            recipe_name=None,
        )
        == "launch"
    )
    row = read_registry(tmp_path)["launch"]
    assert (
        row["claimant_pid"],
        row["claimant_boot_id"],
        row["claimant_starttime_ticks"],
    ) == _CURRENT


def test_claim_refuses_reload_gap_while_claimant_lives_after_child_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"launch":{"claude_session_id":"S-UUID","claimant_pid":701,'
        '"claimant_boot_id":"test-boot","claimant_starttime_ticks":12,'
        '"owner_pid":702,"owner_boot_id":"test-boot","owner_starttime_ticks":13}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(subject, "owner_liveness", lambda pid, *_args: pid == _OTHER[0])
    before = path.read_bytes()

    with pytest.raises(ValueError, match="launch is reserved by a live or unavailable claimant"):
        claim_launch_for_session(
            tmp_path,
            claude_session_id="S-UUID",
            session_type="cook",
            recipe_name=None,
        )

    assert path.read_bytes() == before


@pytest.mark.parametrize("liveness", [True, None])
def test_write_refuses_live_or_unknown_child_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, liveness: bool | None
) -> None:
    _deterministic_identity(monkeypatch)
    write_registry_entry(tmp_path, "launch", "cook", None)
    path = registry_path(tmp_path)
    registry = read_registry(tmp_path)
    registry["launch"].update(
        owner_pid=_CHILD[0],
        owner_boot_id=_CHILD[1],
        owner_starttime_ticks=_CHILD[2],
    )
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(subject, "owner_liveness", lambda *_args: liveness)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="launch has a live or unavailable child owner"):
        write_registry_entry(tmp_path, "launch", "cook", None)

    assert path.read_bytes() == before


def test_write_permits_an_affirmatively_dead_child_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    write_registry_entry(tmp_path, "launch", "cook", None)
    path = registry_path(tmp_path)
    registry = read_registry(tmp_path)
    registry["launch"].update(
        owner_pid=_CHILD[0],
        owner_boot_id=_CHILD[1],
        owner_starttime_ticks=_CHILD[2],
    )
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(subject, "owner_liveness", lambda *_args: False)

    write_registry_entry(tmp_path, "launch", "cook", None)

    assert read_registry(tmp_path)["launch"]["owner_pid"] == _CHILD[0]


def test_bind_requires_recorded_claimant_and_release_is_conditional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _deterministic_identity(monkeypatch)
    monkeypatch.setattr(subject, "_identity_for_pid", lambda _pid: _CHILD)
    write_registry_entry(tmp_path, "launch", "cook", None)
    path = registry_path(tmp_path)
    before = path.read_bytes()

    monkeypatch.setattr(subject, "_current_identity", lambda: _OTHER)
    assert not bind_session_owner(tmp_path, "launch", _CHILD[0])
    assert not release_session_claim(tmp_path, "launch")
    assert path.read_bytes() == before

    monkeypatch.setattr(subject, "_current_identity", lambda: _CURRENT)
    assert bind_session_owner(tmp_path, "launch", _CHILD[0])
    assert release_session_claim(tmp_path, "launch")
    row = read_registry(tmp_path)["launch"]
    assert "claimant_pid" not in row
    assert (row["owner_pid"], row["owner_boot_id"], row["owner_starttime_ticks"]) == _CHILD


def test_claim_serializes_two_cli_reservations(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Barrier(2)
    finish = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(target=_claim_in_child, args=(str(tmp_path), start, finish, outcomes))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=10)
        assert [process.exitcode for process in processes] == [0, 0]
        assert sorted(outcomes.get(timeout=1) for _ in processes) == ["claimed", "refused"]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    claimants = [
        row
        for row in read_registry(tmp_path).values()
        if row.get("claude_session_id") == "shared-session"
    ]
    assert len(claimants) == 1
