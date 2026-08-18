"""Cross-instance and cross-process mutation pacing for GitHub writes."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from autoskillit.execution import GitHubReviewLedger, GitHubReviewMutationCoordinator
from autoskillit.execution.github_review._ledger_schema import MutationSlot

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    async def advance(self, delay: float) -> None:
        self.value += delay


def _claim_and_release(
    ledger: GitHubReviewLedger,
    *,
    now: float,
    owner_token: str,
) -> MutationSlot:
    slot = ledger.claim_mutation_slot(
        scope_id="github-review",
        lease_owner=owner_token,
        operation_key=owner_token,
        now=now,
        minimum_interval_seconds=1.0,
        lease_ttl_seconds=60.0,
    )
    if slot.ready:
        ledger.finish_mutation(
            scope_id="github-review",
            lease_owner=slot.lease_owner,
            lease_generation=slot.lease_generation,
            operation_key=owner_token,
            keep_in_flight=False,
        )
    return slot


def _reserve_in_process(
    database_path: str,
    now: float,
    owner_token: str,
    queue: multiprocessing.Queue,
) -> None:
    try:
        slot = _claim_and_release(
            GitHubReviewLedger(Path(database_path)),
            owner_token=owner_token,
            now=now,
        )
        queue.put(("ok", slot.delay))
    except BaseException as exc:  # pragma: no cover - surfaced in the parent
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def test_mutation_slot_is_durable_across_ledger_instances(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    first = GitHubReviewLedger(database_path)
    second = GitHubReviewLedger(database_path)

    assert _claim_and_release(first, owner_token="owner-a", now=100.0).ready is True
    assert _claim_and_release(
        second,
        owner_token="owner-b",
        now=100.0,
    ).delay == pytest.approx(1.0)


def test_released_owner_observes_the_persisted_interval(
    tmp_path: Path,
) -> None:
    ledger = GitHubReviewLedger(tmp_path / "ledger.sqlite3")
    assert _claim_and_release(ledger, owner_token="same-attempt", now=10.0).ready is True
    assert _claim_and_release(
        ledger,
        owner_token="same-attempt",
        now=10.0,
    ).delay == pytest.approx(1.0)


def test_mutation_slot_backpressure_crosses_process_boundary(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    parent_ledger = GitHubReviewLedger(database_path)
    assert _claim_and_release(parent_ledger, owner_token="parent", now=100.0).ready is True

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
