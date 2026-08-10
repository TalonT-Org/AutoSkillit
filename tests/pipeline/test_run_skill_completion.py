"""Tests for server-owned run_skill completion state."""

from __future__ import annotations

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


def test_acknowledgement_is_one_shot() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    receipt = authority.draft(
        _begin(authority),
        classification="failed",
        success=False,
        result_digest="digest",
    )
    authority.publish(receipt.receipt_id)
    authority.acknowledge(
        receipt.receipt_id,
        kitchen_id="kitchen",
        request_session_id="session",
    )

    with pytest.raises(ValueError, match="already been acknowledged"):
        authority.acknowledge(
            receipt.receipt_id,
            kitchen_id="kitchen",
            request_session_id="session",
        )


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
