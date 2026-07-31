"""Cross-instance and cross-process mutation pacing for GitHub writes."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from autoskillit.execution import GitHubReviewLedger, GitHubReviewMutationCoordinator
from autoskillit.execution.github_review.ledger import MutationSlot

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    async def advance(self, delay: float) -> None:
        self.value += delay


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


async def test_coordinator_observes_backoff_written_by_another_instance(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    writer = GitHubReviewLedger(database_path)
    reader = GitHubReviewLedger(database_path)
    clock = MutableClock(100.0)
    sleeps: list[float] = []

    writer.set_backoff(scope_id="github-review", until=107.0)

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)
        await clock.advance(delay)

    slot = await GitHubReviewMutationCoordinator(
        ledger=reader,
        clock=clock,
        sleeper=sleeper,
    ).acquire(
        scope_id="github-review",
        operation_key="operation-a",
        lease_owner="owner-a",
    )

    assert slot.ready is True
    assert slot.blocked_operation_key is None
    assert sleeps == [pytest.approx(7.0)]


async def test_sleeping_claimant_revalidates_after_lease_expiry_and_second_claim(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    first_ledger = GitHubReviewLedger(database_path)
    second_ledger = GitHubReviewLedger(database_path)
    clock = MutableClock(0.0)
    first_sleeps: list[float] = []
    second_sleeps: list[float] = []
    second_slots: list[MutationSlot] = []

    first_ledger.set_backoff(scope_id="github-review", until=120.0)

    async def second_sleeper(delay: float) -> None:
        second_sleeps.append(delay)
        await clock.advance(delay)

    second = GitHubReviewMutationCoordinator(
        ledger=second_ledger,
        clock=clock,
        sleeper=second_sleeper,
    )

    async def first_sleeper(delay: float) -> None:
        first_sleeps.append(delay)
        clock.value = 61.0
        second_slots.append(
            await second.acquire(
                scope_id="github-review",
                operation_key="operation-b",
                lease_owner="owner-b",
            )
        )

    first = GitHubReviewMutationCoordinator(
        ledger=first_ledger,
        clock=clock,
        sleeper=first_sleeper,
    )
    first_slot = await first.acquire(
        scope_id="github-review",
        operation_key="operation-a",
        lease_owner="owner-a",
    )

    assert first_sleeps == [pytest.approx(120.0)]
    assert second_sleeps == [pytest.approx(59.0)]
    assert second_slots[0].ready is True
    assert first_slot.ready is False
    assert first_slot.blocked_operation_key == "operation-b"
