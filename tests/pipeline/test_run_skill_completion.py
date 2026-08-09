"""Tests for server-owned run_skill completion state."""

from __future__ import annotations

import pytest

from autoskillit.pipeline import DefaultRunSkillCompletionAuthority

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _begin(authority: DefaultRunSkillCompletionAuthority, *, session: str = "session") -> str:
    return authority.begin(
        kitchen_id="kitchen",
        request_session_id=session,
        tracker_order_id="order",
        tracker_path="/tracker.json",
        tracker_kitchen_id="kitchen",
        tracker_incarnation_id="incarnation",
        step_name="investigate-2",
    )


def test_parallel_invocations_publish_distinct_receipts() -> None:
    authority = DefaultRunSkillCompletionAuthority()
    first = _begin(authority)
    second = _begin(authority)

    first_receipt = authority.draft(
        first, classification="success", success=True, result_digest="one"
    )
    second_receipt = authority.draft(
        second, classification="timeout", success=False, result_digest="two"
    )

    assert first_receipt.receipt_id != second_receipt.receipt_id
    assert authority.admission("run_skill") == (
        False,
        "result awaiting acknowledgement",
    )
    authority.publish(second_receipt.receipt_id)
    authority.publish(first_receipt.receipt_id)
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
    authority.publish(receipt.receipt_id)
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
