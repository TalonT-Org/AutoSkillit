"""Cross-instance and cross-process mutation pacing for GitHub writes."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from autoskillit.execution import GitHubReviewLedger

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _reserve_in_process(
    database_path: str,
    now: float,
    owner_token: str,
    queue: multiprocessing.Queue,
) -> None:
    try:
        delay = GitHubReviewLedger(Path(database_path)).reserve_mutation_slot(
            scope_id="github-review",
            owner_token=owner_token,
            now=now,
            minimum_interval_seconds=1.0,
        )
        queue.put(("ok", delay))
    except BaseException as exc:  # pragma: no cover - surfaced in the parent
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def test_mutation_slot_is_durable_across_ledger_instances(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    first = GitHubReviewLedger(database_path)
    second = GitHubReviewLedger(database_path)

    assert (
        first.reserve_mutation_slot(
            scope_id="github-review",
            owner_token="owner-a",
            now=100.0,
            minimum_interval_seconds=1.0,
        )
        == 0.0
    )
    assert second.reserve_mutation_slot(
        scope_id="github-review",
        owner_token="owner-b",
        now=100.0,
        minimum_interval_seconds=1.0,
    ) == pytest.approx(1.0)


def test_same_owner_can_reacquire_reserved_slot_without_double_delay(
    tmp_path: Path,
) -> None:
    ledger = GitHubReviewLedger(tmp_path / "ledger.sqlite3")
    assert (
        ledger.reserve_mutation_slot(
            scope_id="github-review",
            owner_token="same-attempt",
            now=10.0,
            minimum_interval_seconds=1.0,
        )
        == 0.0
    )
    assert (
        ledger.reserve_mutation_slot(
            scope_id="github-review",
            owner_token="same-attempt",
            now=10.0,
            minimum_interval_seconds=1.0,
        )
        == 0.0
    )


def test_mutation_slot_backpressure_crosses_process_boundary(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    parent_ledger = GitHubReviewLedger(database_path)
    assert (
        parent_ledger.reserve_mutation_slot(
            scope_id="github-review",
            owner_token="parent",
            now=100.0,
            minimum_interval_seconds=1.0,
        )
        == 0.0
    )

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(
        target=_reserve_in_process,
        args=(str(database_path), 100.0, "child", queue),
    )
    process.start()
    process.join(timeout=20)
    assert not process.is_alive(), "child reservation process did not terminate"
    assert process.exitcode == 0
    status, value = queue.get(timeout=5)
    assert status == "ok", value
    assert value == pytest.approx(1.0)
