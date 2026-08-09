"""Parallel-safe completion authority for ``run_skill`` delivery."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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


class DefaultRunSkillCompletionAuthority:
    """Own in-flight work, delivery receipts, and tracker repair credits."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._in_flight: dict[str, _Invocation] = {}
        self._drafts: dict[str, RunSkillCompletionReceipt] = {}
        self._delivered: dict[str, RunSkillCompletionReceipt] = {}
        self._consumed: set[str] = set()
        self._credits: dict[tuple[str, str, str, str, str], set[str]] = {}

    def admission(self, tool_name: str) -> tuple[bool, str]:
        """Return whether ``tool_name`` is admissible in the current phase."""
        with self._lock:
            if self._drafts or self._delivered:
                return tool_name == "complete_run_skill_result", "result awaiting acknowledgement"
            if self._in_flight:
                return tool_name in {
                    "run_skill",
                    "complete_run_skill_result",
                }, "run_skill invocation in flight"
            return True, "idle"

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
            )
            return invocation_id

    def abort(self, invocation_id: str) -> None:
        """Remove an invocation that did not publish a terminal result."""
        with self._lock:
            if self._in_flight.pop(invocation_id, None) is None:
                raise ValueError("unknown run_skill invocation")

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
            )
            self._drafts[receipt.receipt_id] = receipt
            return receipt

    def publish(self, receipt_id: str) -> RunSkillCompletionReceipt:
        """Promote an exactly preserved draft to acknowledgeable delivery."""
        with self._lock:
            receipt = self._drafts.pop(receipt_id, None)
            if receipt is None:
                raise ValueError("unknown or already-published run_skill receipt")
            self._delivered[receipt_id] = receipt
            return receipt

    def acknowledge(
        self,
        receipt_id: str,
        *,
        kitchen_id: str,
        request_session_id: str,
    ) -> RunSkillCompletionReceipt:
        """Consume one delivered receipt from its launching request session."""
        with self._lock:
            if receipt_id in self._consumed:
                raise ValueError("run_skill receipt has already been acknowledged")
            receipt = self._delivered.get(receipt_id)
            if receipt is None:
                raise ValueError("run_skill receipt is not available for acknowledgement")
            if receipt.kitchen_id != kitchen_id:
                raise ValueError("run_skill receipt belongs to another kitchen")
            if receipt.request_session_id != request_session_id:
                raise ValueError("run_skill receipt belongs to another request session")
            del self._delivered[receipt_id]
            self._consumed.add(receipt_id)
            if receipt.success and receipt.tracker_incarnation_id:
                self._credits.setdefault(self._credit_key(receipt), set()).add(receipt_id)
            return receipt

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
            if self._in_flight or self._drafts or self._delivered:
                return False
            self._credits.clear()
            self._consumed.clear()
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
