"""Parallel-safe completion authority for ``run_skill`` delivery."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from autoskillit.pipeline.tokens import canonical_step_name

__all__ = ["DefaultRunSkillCompletionAuthority", "RunSkillCompletionReceipt"]


@dataclass(frozen=True, slots=True)
class RunSkillCompletionReceipt:
    """Opaque receipt plus server-derived execution bindings."""

    receipt_id: str
    kitchen_id: str
    request_session_id: str
    invocation_id: str
    tracker_order_id: str
    tracker_path: str
    tracker_kitchen_id: str
    tracker_incarnation_id: str
    step_name: str
    child_session_id: str
    classification: str
    success: bool
    result_digest: str
    started_at: float


@dataclass(frozen=True, slots=True)
class _Invocation:
    invocation_id: str
    kitchen_id: str
    request_session_id: str
    tracker_order_id: str
    tracker_path: str
    tracker_kitchen_id: str
    tracker_incarnation_id: str
    step_name: str
    started_at: float


@dataclass(frozen=True, slots=True)
class _AcknowledgedReceipt:
    receipt: RunSkillCompletionReceipt
    tracker_outcome: Mapping[str, Any] | None = None


class DefaultRunSkillCompletionAuthority:
    """Own in-flight work, delivery receipts, and tracker repair credits."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tracker_outcome_condition = threading.Condition(self._lock)
        self._tracker_outcomes_in_flight: set[str] = set()
        self._in_flight: dict[str, _Invocation] = {}
        self._drafts: dict[str, RunSkillCompletionReceipt] = {}
        self._delivered: dict[str, RunSkillCompletionReceipt] = {}
        self._recovered: set[str] = set()
        self._acknowledged: dict[str, _AcknowledgedReceipt] = {}
        self._credits: dict[tuple[str, str, str, str, str], set[str]] = {}

    def admission(self, tool_name: str) -> tuple[bool, str]:
        """Return whether ``tool_name`` is admissible in the current phase."""
        with self._lock:
            if self._drafts or self._delivered:
                return tool_name in {
                    "complete_run_skill_result",
                    "recover_run_skill_result",
                }, "result awaiting acknowledgement"
            if self._in_flight:
                return tool_name in {
                    "run_skill",
                    "complete_run_skill_result",
                }, "run_skill invocation in flight"
            return True, "idle"

    def pending_info(self, tool_name: str) -> dict[str, object] | None:
        """Return ``{step_name, elapsed_seconds}`` for the oldest pending record.

        ``tool_name`` is accepted for call-site parity but ignored; at most one
        record is active at a time, so the priority order across drafts/delivered
        and in-flight resolves deterministically.
        """
        del tool_name
        with self._lock:
            candidates: list[tuple[float, str]] = []
            if self._drafts or self._delivered:
                candidates = [
                    (receipt.started_at, receipt.step_name)
                    for receipt in (*self._drafts.values(), *self._delivered.values())
                ]
            elif self._in_flight:
                candidates = [
                    (invocation.started_at, invocation.step_name)
                    for invocation in self._in_flight.values()
                ]
            if not candidates:
                return None
            started_at, step_name = min(candidates, key=lambda candidate: candidate[0])
            return {"step_name": step_name, "elapsed_seconds": time.monotonic() - started_at}

    def begin(
        self,
        *,
        kitchen_id: str,
        request_session_id: str,
        tracker_order_id: str,
        tracker_path: str,
        tracker_kitchen_id: str,
        tracker_incarnation_id: str,
        step_name: str,
    ) -> str:
        """Atomically admit and register a new invocation."""
        with self._lock:
            if self._drafts or self._delivered:
                raise RuntimeError("a run_skill result is awaiting acknowledgement")
            invocation_id = uuid.uuid4().hex
            self._in_flight[invocation_id] = _Invocation(
                invocation_id=invocation_id,
                kitchen_id=kitchen_id,
                request_session_id=request_session_id,
                tracker_order_id=tracker_order_id,
                tracker_path=tracker_path,
                tracker_kitchen_id=tracker_kitchen_id,
                tracker_incarnation_id=tracker_incarnation_id,
                step_name=canonical_step_name(step_name),
                started_at=time.monotonic(),
            )
            return invocation_id

    def draft(
        self,
        invocation_id: str,
        *,
        classification: str,
        success: bool,
        result_digest: str,
        child_session_id: str = "",
    ) -> RunSkillCompletionReceipt:
        """Atomically replace an in-flight invocation with a draft receipt."""
        with self._lock:
            invocation = self._in_flight.pop(invocation_id, None)
            if invocation is None:
                raise ValueError("unknown run_skill invocation")
            receipt = RunSkillCompletionReceipt(
                receipt_id=uuid.uuid4().hex,
                kitchen_id=invocation.kitchen_id,
                request_session_id=invocation.request_session_id,
                invocation_id=invocation.invocation_id,
                tracker_order_id=invocation.tracker_order_id,
                tracker_path=invocation.tracker_path,
                tracker_kitchen_id=invocation.tracker_kitchen_id,
                tracker_incarnation_id=invocation.tracker_incarnation_id,
                step_name=invocation.step_name,
                child_session_id=child_session_id,
                classification=classification,
                success=success,
                result_digest=result_digest,
                started_at=invocation.started_at,
            )
            self._drafts[receipt.receipt_id] = receipt
            return receipt

    def abort(self, invocation_id: str) -> bool:
        """Discard one exact invocation that escaped before drafting."""
        with self._lock:
            return self._in_flight.pop(invocation_id, None) is not None

    def discard_draft(self, receipt_id: str) -> bool:
        """Discard one exact draft that escaped before publication."""
        with self._lock:
            return self._drafts.pop(receipt_id, None) is not None

    def publish(self, receipt_id: str) -> RunSkillCompletionReceipt:
        """Promote an exactly preserved draft to acknowledgeable delivery."""
        with self._lock:
            receipt = self._drafts.pop(receipt_id, None)
            if receipt is None:
                raise ValueError("unknown or already-published run_skill receipt")
            self._delivered[receipt_id] = receipt
            return receipt

    def recover(
        self,
        *,
        kitchen_id: str,
        request_session_id: str,
    ) -> RunSkillCompletionReceipt:
        """Rebind the sole delivered receipt to one replacement request session."""
        with self._lock:
            if len(self._delivered) > 1:
                raise ValueError("multiple run_skill receipts are awaiting acknowledgement")
            if not self._delivered:
                if self._drafts:
                    raise ValueError("run_skill receipt has not been delivered")
                if self._acknowledged:
                    raise ValueError("run_skill receipt has already been acknowledged")
                raise ValueError("no delivered run_skill receipt is available for recovery")
            receipt_id, receipt = next(iter(self._delivered.items()))
            if receipt.kitchen_id != kitchen_id:
                raise ValueError("run_skill receipt belongs to another kitchen")
            if receipt_id in self._recovered:
                raise ValueError("run_skill receipt has already been recovered")
            recovered = replace(receipt, request_session_id=request_session_id)
            self._delivered[receipt_id] = recovered
            self._recovered.add(receipt_id)
            return recovered

    def acknowledge(
        self,
        receipt_id: str,
        *,
        kitchen_id: str,
        request_session_id: str,
    ) -> RunSkillCompletionReceipt:
        """Acknowledge one receipt, replaying its authenticated live-process record."""
        with self._lock:
            acknowledged = self._acknowledged.get(receipt_id)
            if acknowledged is not None:
                receipt = acknowledged.receipt
                if receipt.kitchen_id != kitchen_id:
                    raise ValueError("run_skill receipt belongs to another kitchen")
                if receipt.request_session_id != request_session_id:
                    raise ValueError("run_skill receipt belongs to another request session")
                return receipt
            delivered_receipt = self._delivered.get(receipt_id)
            if delivered_receipt is None:
                raise ValueError("run_skill receipt is not available for acknowledgement")
            if delivered_receipt.kitchen_id != kitchen_id:
                raise ValueError("run_skill receipt belongs to another kitchen")
            if delivered_receipt.request_session_id != request_session_id:
                raise ValueError("run_skill receipt belongs to another request session")
            del self._delivered[receipt_id]
            self._recovered.discard(receipt_id)
            self._acknowledged[receipt_id] = _AcknowledgedReceipt(receipt=delivered_receipt)
            if delivered_receipt.success and delivered_receipt.tracker_incarnation_id:
                self._credits.setdefault(self._credit_key(delivered_receipt), set()).add(
                    receipt_id
                )
            return delivered_receipt

    def apply_acknowledged_tracker_outcome(
        self,
        receipt_id: str,
        *,
        kitchen_id: str,
        request_session_id: str,
        effect: Callable[[], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Run and cache the tracker outcome for one acknowledged receipt."""
        with self._tracker_outcome_condition:
            while True:
                acknowledged = self._acknowledged.get(receipt_id)
                if acknowledged is None:
                    raise ValueError("run_skill receipt has not been acknowledged")
                receipt = acknowledged.receipt
                if receipt.kitchen_id != kitchen_id:
                    raise ValueError("run_skill receipt belongs to another kitchen")
                if receipt.request_session_id != request_session_id:
                    raise ValueError("run_skill receipt belongs to another request session")
                if acknowledged.tracker_outcome is not None:
                    return dict(acknowledged.tracker_outcome)
                if receipt_id not in self._tracker_outcomes_in_flight:
                    self._tracker_outcomes_in_flight.add(receipt_id)
                    break
                self._tracker_outcome_condition.wait()

        try:
            outcome = dict(effect())
        except BaseException:
            with self._tracker_outcome_condition:
                self._tracker_outcomes_in_flight.remove(receipt_id)
                self._tracker_outcome_condition.notify_all()
            raise

        with self._tracker_outcome_condition:
            try:
                self._acknowledged[receipt_id] = replace(
                    acknowledged,
                    tracker_outcome=outcome,
                )
            finally:
                self._tracker_outcomes_in_flight.remove(receipt_id)
                self._tracker_outcome_condition.notify_all()
            return dict(outcome)

    def apply_tracker_credit(
        self,
        *,
        tracker_order_id: str,
        tracker_path: str,
        tracker_kitchen_id: str,
        tracker_incarnation_id: str,
        step_name: str,
        effect: Callable[[], Mapping[str, Any]],
        receipt_id: str = "",
    ) -> Mapping[str, Any]:
        """Run one synchronous tracker effect and consume its matching credit."""
        key = (
            tracker_order_id,
            tracker_path,
            tracker_kitchen_id,
            tracker_incarnation_id,
            canonical_step_name(step_name),
        )
        with self._lock:
            credits = self._credits.get(key)
            if not credits:
                stale_keys = [
                    candidate
                    for candidate in self._credits
                    if candidate[0] == tracker_order_id
                    and candidate[1] == tracker_path
                    and candidate[4] == canonical_step_name(step_name)
                ]
                for stale_key in stale_keys:
                    del self._credits[stale_key]
            selected = receipt_id or (min(credits) if credits else "")
            if not credits or selected not in credits:
                raise ValueError("no acknowledged success credit matches this tracker step")
            result = dict(effect())
            if result.get("incarnation_matches") is False:
                del self._credits[key]
            elif result.get("success") and result.get("status") == "complete":
                credits.remove(selected)
                if not credits:
                    del self._credits[key]
            return result

    def clear_if_idle(self) -> bool:
        """Clear retained credits only when no completion is active."""
        with self._lock:
            if (
                self._in_flight
                or self._drafts
                or self._delivered
                or self._tracker_outcomes_in_flight
            ):
                return False
            self._credits.clear()
            self._recovered.clear()
            self._acknowledged.clear()
            return True

    @staticmethod
    def _credit_key(receipt: RunSkillCompletionReceipt) -> tuple[str, str, str, str, str]:
        return (
            receipt.tracker_order_id,
            receipt.tracker_path,
            receipt.tracker_kitchen_id,
            receipt.tracker_incarnation_id,
            receipt.step_name,
        )
