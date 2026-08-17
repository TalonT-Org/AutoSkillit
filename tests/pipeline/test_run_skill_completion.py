"""Tests for server-owned run_skill completion state."""

from __future__ import annotations

import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from autoskillit.pipeline import DefaultRunSkillCompletionAuthority, RunSkillCompletionReceipt

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _begin(
    authority: DefaultRunSkillCompletionAuthority,
    *,
    kitchen: str = "kitchen",
    session: str = "session",
) -> str:
    return authority.begin(
        kitchen_id=kitchen,
        request_session_id=session,
        tracker_order_id="order",
        tracker_path="/tracker.json",
        tracker_kitchen_id="kitchen",
        tracker_incarnation_id="incarnation",
        step_name="investigate-2",
    )


def _publish(
    authority: DefaultRunSkillCompletionAuthority,
    *,
    kitchen: str = "kitchen",
    session: str = "session",
) -> RunSkillCompletionReceipt:
    receipt = authority.draft(
        _begin(authority, kitchen=kitchen, session=session),
        classification="success",
        success=True,
        result_digest="digest",
    )
    return authority.publish(receipt.receipt_id)


def test_parallel_invocations_publish_distinct_receipts() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    invocations_started = Barrier(2)

    def publish(classification: str, success: bool, digest: str):
        invocation = _begin(authority)
        invocations_started.wait()
        receipt = authority.draft(
            invocation,
            classification=classification,
            success=success,
            result_digest=digest,
        )
        return authority.publish(receipt.receipt_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(publish, "success", True, "one")
        second_future = pool.submit(publish, "timeout", False, "two")
        first_receipt = first_future.result()
        second_receipt = second_future.result()

    assert first_receipt.receipt_id != second_receipt.receipt_id
    assert authority.admission("run_skill") == (
        False,
        "result awaiting acknowledgement",
    )
    authority.acknowledge(
        second_receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    authority.acknowledge(
        first_receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )


def test_abort_removes_only_the_exact_in_flight_invocation() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    first = _begin(authority)
    sibling = _begin(authority)

    assert authority.abort(first) is True
    assert authority.abort(first) is False
    with pytest.raises(ValueError, match="unknown run_skill invocation"):
        authority.draft(
            first,
            classification="success",
            success=True,
            result_digest="first",
        )

    sibling_receipt = authority.draft(
        sibling,
        classification="success",
        success=True,
        result_digest="sibling",
    )
    authority.publish(sibling_receipt.receipt_id)
    authority.acknowledge(
        sibling_receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    assert authority.admission("kitchen_status") == (True, "idle")


def test_discard_draft_removes_only_the_exact_unpublished_receipt() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    first = _begin(authority)
    sibling = _begin(authority)
    first_receipt = authority.draft(
        first,
        classification="success",
        success=True,
        result_digest="first",
    )

    assert authority.discard_draft(first_receipt.receipt_id) is True
    assert authority.discard_draft(first_receipt.receipt_id) is False
    with pytest.raises(ValueError, match="unknown or already-published"):
        authority.publish(first_receipt.receipt_id)

    sibling_receipt = authority.draft(
        sibling,
        classification="success",
        success=True,
        result_digest="sibling",
    )
    authority.publish(sibling_receipt.receipt_id)
    authority.acknowledge(
        sibling_receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )

    delivered = _publish(authority)
    assert authority.discard_draft(delivered.receipt_id) is False
    authority.acknowledge(
        delivered.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    assert authority.admission("kitchen_status") == (True, "idle")


@pytest.mark.parametrize(
    ("kitchen_id", "request_session_id"),
    [("other", "session"), ("kitchen", "other")],
)
def test_acknowledgement_rejects_wrong_binding(kitchen_id: str, request_session_id: str) -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = authority.draft(
        _begin(authority),
        classification="success",
        success=True,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)

    with pytest.raises(ValueError):
        authority.acknowledge(
            receipt.receipt_id,
            kitchen_id=kitchen_id,
            request_session_id=request_session_id,
        )


def test_acknowledgement_replays_for_the_same_kitchen_and_request_session() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = authority.draft(
        _begin(authority),
        classification="failed",
        success=False,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)
    first = authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    second = authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    assert first == second == receipt


def test_acknowledged_tracker_outcome_is_cached_exactly_once() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = _publish(authority)
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    effects: list[str] = []

    def effect() -> dict[str, object]:
        effects.append("applied")
        return {"success": True, "status": "complete"}

    first = authority.apply_acknowledged_tracker_outcome(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
        effect=effect,
    )
    second = authority.apply_acknowledged_tracker_outcome(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
        effect=effect,
    )
    assert first == second == {"success": True, "status": "complete"}
    assert effects == ["applied"]


def test_acknowledged_tracker_effect_releases_authority_lock() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = _publish(authority)
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    effect_started = Event()
    release_effect = Event()
    effects: list[str] = []

    def effect() -> dict[str, object]:
        effects.append("applied")
        effect_started.set()
        if not release_effect.wait(timeout=5):
            raise AssertionError("test did not release tracker effect")
        return {"success": True, "status": "complete"}

    def apply_outcome() -> Mapping[str, object]:
        return authority.apply_acknowledged_tracker_outcome(
            receipt.receipt_id,
            kitchen_id="kitchen",
            request_session_id="session",
            effect=effect,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(apply_outcome)
        assert effect_started.wait(timeout=5)
        second = executor.submit(apply_outcome)
        admission = executor.submit(authority.admission, "run_cmd")
        try:
            assert admission.result(timeout=5) == (True, "idle")
        finally:
            release_effect.set()
        assert first.result(timeout=5) == {"success": True, "status": "complete"}
        assert second.result(timeout=5) == {"success": True, "status": "complete"}

    assert effects == ["applied"]


def test_recovery_rebinds_the_sole_delivered_receipt_once() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = _publish(authority, session="disconnected")

    assert authority.admission("recover_run_skill_result")[0] is True
    recovered = authority.recover(
        kitchen_id="kitchen",
        request_session_id="replacement",
    )

    assert recovered.receipt_id == receipt.receipt_id
    assert recovered.request_session_id == "replacement"
    with pytest.raises(ValueError, match="already been recovered"):
        authority.recover(
            kitchen_id="kitchen",
            request_session_id="third-session",
        )
    assert (
        authority.acknowledge(
            receipt.receipt_id,
            kitchen_id="kitchen",
            request_session_id="replacement",
        )
        == recovered
    )


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("draft", "has not been delivered"),
        ("consumed", "already been acknowledged"),
        ("multiple", "multiple run_skill receipts"),
        ("wrong_kitchen", "belongs to another kitchen"),
    ],
)
def test_recovery_refuses_nonrecoverable_receipt_states(state: str, message: str) -> None:
    authority = DefaultRunSkillCompletionAuthority()
    if state == "draft":
        authority.draft(
            _begin(authority),
            classification="success",
            success=True,
            result_digest="digest",
        )
    elif state == "consumed":
        receipt = _publish(authority)
        authority.acknowledge(
            receipt.receipt_id,
            kitchen_id="kitchen",
            request_session_id="session",
        )
    elif state == "multiple":
        first = _begin(authority)
        second = _begin(authority)
        for invocation in (first, second):
            receipt = authority.draft(
                invocation,
                classification="success",
                success=True,
                result_digest="digest",
            )
            authority.publish(receipt.receipt_id)
    else:
        _publish(authority, kitchen="other-kitchen")

    with pytest.raises(ValueError, match=message):
        authority.recover(
            kitchen_id="kitchen",
            request_session_id="replacement",
        )


def test_success_credit_is_retained_until_tracker_completes() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = authority.draft(
        _begin(authority),
        classification="success",
        success=True,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    binding = {
        "tracker_order_id": "order",
        "tracker_path": "/tracker.json",
        "tracker_kitchen_id": "kitchen",
        "tracker_incarnation_id": "incarnation",
        "step_name": "investigate",
    }

    failed = authority.apply_tracker_credit(
        **binding,
        receipt_id=receipt.receipt_id,
        effect=lambda: {"success": False, "status": "pending"},
    )
    completed = authority.apply_tracker_credit(
        **binding,
        receipt_id=receipt.receipt_id,
        effect=lambda: {"success": True, "status": "complete"},
    )

    assert failed["status"] == "pending"
    assert completed["status"] == "complete"
    with pytest.raises(ValueError, match="no acknowledged success credit"):
        authority.apply_tracker_credit(
            **binding,
            effect=lambda: {"success": True, "status": "complete"},
        )


def test_omitted_receipt_id_consumes_oldest_matching_credit() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipts = []
    for _ in range(2):
        receipt = _publish(authority)
        authority.acknowledge(
            receipt.receipt_id,
            kitchen_id="kitchen",
            request_session_id="session",
        )
        receipts.append(receipt)
    oldest, remaining = sorted(receipt.receipt_id for receipt in receipts)
    binding = {
        "tracker_order_id": "order",
        "tracker_path": "/tracker.json",
        "tracker_kitchen_id": "kitchen",
        "tracker_incarnation_id": "incarnation",
        "step_name": "investigate",
    }

    authority.apply_tracker_credit(
        **binding,
        effect=lambda: {"success": True, "status": "complete"},
    )

    with pytest.raises(ValueError, match="no acknowledged success credit"):
        authority.apply_tracker_credit(
            **binding,
            receipt_id=oldest,
            effect=lambda: {"success": True, "status": "complete"},
        )
    authority.apply_tracker_credit(
        **binding,
        receipt_id=remaining,
        effect=lambda: {"success": True, "status": "complete"},
    )


def test_acknowledgement_and_two_repairs_consume_credit_exactly_once() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = authority.draft(
        _begin(authority),
        classification="success",
        success=True,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)
    acknowledged = Event()
    contenders = Barrier(3)
    effects: list[str] = []
    binding = {
        "tracker_order_id": "order",
        "tracker_path": "/tracker.json",
        "tracker_kitchen_id": "kitchen",
        "tracker_incarnation_id": "incarnation",
        "step_name": "investigate",
        "receipt_id": receipt.receipt_id,
    }

    def consume() -> bool:
        contenders.wait()
        try:
            authority.apply_tracker_credit(
                **binding,
                effect=lambda: (
                    effects.append("complete") or {"success": True, "status": "complete"}
                ),
            )
        except ValueError:
            return False
        return True

    def acknowledge_and_consume() -> bool:
        authority.acknowledge(
            receipt.receipt_id,
            kitchen_id="kitchen",
            request_session_id="session",
        )
        acknowledged.set()
        return consume()

    def repair() -> bool:
        assert acknowledged.wait(timeout=1)
        return consume()

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = [
            pool.submit(acknowledge_and_consume),
            pool.submit(repair),
            pool.submit(repair),
        ]

    assert sum(future.result() for future in outcomes) == 1
    assert effects == ["complete"]


def test_reinitialized_tracker_invalidates_retained_credit() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = authority.draft(
        _begin(authority),
        classification="success",
        success=True,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )

    replacement = {
        "tracker_order_id": "order",
        "tracker_path": "/tracker.json",
        "tracker_kitchen_id": "kitchen",
        "tracker_incarnation_id": "replacement-incarnation",
        "step_name": "investigate",
    }
    with pytest.raises(ValueError, match="no acknowledged success credit"):
        authority.apply_tracker_credit(
            **replacement,
            effect=lambda: {"success": True, "status": "complete"},
        )
    with pytest.raises(ValueError, match="no acknowledged success credit"):
        authority.apply_tracker_credit(
            tracker_order_id="order",
            tracker_path="/tracker.json",
            tracker_kitchen_id="kitchen",
            tracker_incarnation_id="incarnation",
            step_name="investigate",
            effect=lambda: {"success": True, "status": "complete"},
        )


def test_clear_is_denied_while_active_and_removes_idle_credit() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    invocation = _begin(authority)

    assert authority.clear_if_idle() is False
    receipt = authority.draft(
        invocation,
        classification="success",
        success=True,
        result_digest="digest",
    )
    assert authority.clear_if_idle() is False
    authority.publish(receipt.receipt_id)
    assert authority.clear_if_idle() is False
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )
    assert authority.clear_if_idle() is True

    with pytest.raises(ValueError, match="no acknowledged success credit"):
        authority.apply_tracker_credit(
            tracker_order_id="order",
            tracker_path="/tracker.json",
            tracker_kitchen_id="kitchen",
            tracker_incarnation_id="incarnation",
            step_name="investigate",
            effect=lambda: {"success": True, "status": "complete"},
        )


def test_begin_records_started_at() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    before = time.monotonic()
    invocation_id = _begin(authority)
    after = time.monotonic()

    invocation = authority._in_flight[invocation_id]
    assert before <= invocation.started_at <= after


def test_started_at_survives_onto_receipt_via_draft() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    invocation_id = _begin(authority)
    recorded = authority._in_flight[invocation_id].started_at

    receipt = authority.draft(
        invocation_id,
        classification="success",
        success=True,
        result_digest="digest",
    )
    assert receipt.started_at == recorded


class TestPendingInfo:
    """pending_info() sources from whichever collection is authoritative."""

    def test_returns_none_when_idle(self) -> None:
        authority = DefaultRunSkillCompletionAuthority()
        assert authority.pending_info("run_skill") is None

    def test_in_flight_only(self) -> None:
        authority = DefaultRunSkillCompletionAuthority()
        _begin(authority)
        time.sleep(0.02)

        info = authority.pending_info("run_skill")

        assert info is not None
        assert info["step_name"] == "investigate"
        assert info["elapsed_seconds"] >= 0.02

    def test_delivered_only(self) -> None:
        """The actual shape of the 88-minute incident: _drafts is empty, _delivered holds it."""
        authority = DefaultRunSkillCompletionAuthority()
        _publish(authority)
        time.sleep(0.02)

        info = authority.pending_info("run_skill")

        assert info is not None
        assert info["step_name"] == "investigate"
        assert info["elapsed_seconds"] >= 0.02

    def test_oldest_entry_wins_when_multiple_in_flight(self) -> None:
        """begin() only refuses new work while drafts/delivered are non-empty — a second
        begin() before the first is drafted leaves two entries in _in_flight."""
        authority = DefaultRunSkillCompletionAuthority()
        _begin(authority, session="older")
        time.sleep(0.02)
        authority.begin(
            kitchen_id="kitchen",
            request_session_id="newer",
            tracker_order_id="order",
            tracker_path="/tracker.json",
            tracker_kitchen_id="kitchen",
            tracker_incarnation_id="incarnation",
            step_name="second-step",
        )

        info = authority.pending_info("run_skill")

        assert info is not None
        assert info["step_name"] == "investigate"
        assert info["elapsed_seconds"] >= 0.02
