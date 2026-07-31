"""Differential state-machine coverage for the durable audit-admission ledger."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from autoskillit.core import (
    AuditAdmissionStorageHealthStatus,
    AuditFinalCommitRequest,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPrepareRequest,
    AuditReservationOutcome,
    AuditReservationRequest,
    AuditVerdict,
    InstallationVersion,
    RecipeExecutionId,
    ReservationDecision,
)
from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger
from tests.pipeline.test_audit_admission_ledger import (
    _authority,
    _digest,
    _head,
    _plan_set_id,
    _reservation_request,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.medium]

_REQUIRED_FINALIZATION_EFFECTS = (
    "audit_success_recorded",
    "run_skill_state_cleared",
)


class _ReferenceLifecycle(Enum):
    OPEN = auto()
    REJECTED = auto()
    PREPARED = auto()
    PUBLISHED = auto()
    RESPONSE_COMMITTED = auto()


class _ReferenceDecision(Enum):
    REDISPATCH = auto()
    RESUME_PREPARED = auto()
    RESUME_PUBLISHED = auto()
    EXACT_REPLAY = auto()
    CONFLICT = auto()


class _ReferenceCommit(Enum):
    PUBLISH = auto()
    STALE_HEAD = auto()
    TERMINAL_HEAD = auto()


_PRODUCTION_DECISIONS = {
    _ReferenceDecision.REDISPATCH: ReservationDecision.REDISPATCH_OPEN,
    _ReferenceDecision.RESUME_PREPARED: ReservationDecision.RESUME_PREPARED,
    _ReferenceDecision.RESUME_PUBLISHED: (ReservationDecision.PUBLISHED_PENDING_FINALIZATION),
    _ReferenceDecision.EXACT_REPLAY: ReservationDecision.EXACT_REPLAY,
    _ReferenceDecision.CONFLICT: ReservationDecision.CONFLICT,
}


@dataclass(slots=True)
class _ReferenceAttempt:
    identifier: str
    lifecycle: _ReferenceLifecycle
    handle: str | None
    semantic_digest: str | None = None
    correction_predecessor: str | None = None
    acknowledged_effects: set[str] = field(default_factory=set)
    replay_response_json: str | None = None


class _AuditReferenceModel:
    """Pure transition oracle; opaque production identities are observations only."""

    def __init__(self) -> None:
        self.active = False
        self.installation_version: str | None = None
        self.installation_occurrences: list[str] = []
        self.slots: dict[str, _ReferenceAttempt] = {}
        self.all_attempt_ids: set[str] = set()
        self.consumed_correction_tokens: set[str] = set()
        self.head_digest: str | None = None
        self.head_is_terminal = False

    def begin_installation(self, version: str) -> None:
        assert not self.active
        assert version not in self.installation_occurrences
        self.active = True
        self.installation_version = version
        self.installation_occurrences.append(version)
        self.slots = {}

    def retire_installation(self) -> None:
        assert self.active
        self.active = False

    def register_slot(self, label: str, attempt_id: str, handle: str) -> None:
        assert self.active
        assert label not in self.slots
        assert attempt_id not in self.all_attempt_ids
        self.slots[label] = _ReferenceAttempt(
            identifier=attempt_id,
            lifecycle=_ReferenceLifecycle.OPEN,
            handle=handle,
        )
        self.all_attempt_ids.add(attempt_id)

    def attempt(self, label: str) -> _ReferenceAttempt:
        return self.slots[label]

    def expected_redelivery(self, label: str) -> _ReferenceDecision:
        if not self.active:
            return _ReferenceDecision.CONFLICT
        lifecycle = self.attempt(label).lifecycle
        return {
            _ReferenceLifecycle.OPEN: _ReferenceDecision.REDISPATCH,
            _ReferenceLifecycle.REJECTED: _ReferenceDecision.CONFLICT,
            _ReferenceLifecycle.PREPARED: _ReferenceDecision.RESUME_PREPARED,
            _ReferenceLifecycle.PUBLISHED: _ReferenceDecision.RESUME_PUBLISHED,
            _ReferenceLifecycle.RESPONSE_COMMITTED: _ReferenceDecision.EXACT_REPLAY,
        }[lifecycle]

    def rotate_handle(self, label: str, handle: str) -> None:
        attempt = self.attempt(label)
        assert attempt.lifecycle is _ReferenceLifecycle.OPEN
        assert handle != attempt.handle
        attempt.handle = handle

    def prepare(self, label: str, *, accepted: bool, semantic_digest: str) -> None:
        attempt = self.attempt(label)
        assert self.active
        assert attempt.lifecycle is _ReferenceLifecycle.OPEN
        attempt.semantic_digest = semantic_digest
        attempt.handle = None
        attempt.lifecycle = (
            _ReferenceLifecycle.PREPARED if accepted else _ReferenceLifecycle.REJECTED
        )

    def advance_correction(self, label: str, attempt_id: str, handle: str) -> None:
        predecessor = self.attempt(label)
        assert self.active
        assert predecessor.lifecycle is _ReferenceLifecycle.REJECTED
        assert predecessor.identifier not in self.consumed_correction_tokens
        self.consumed_correction_tokens.add(predecessor.identifier)
        assert attempt_id not in self.all_attempt_ids
        self.slots[label] = _ReferenceAttempt(
            identifier=attempt_id,
            lifecycle=_ReferenceLifecycle.OPEN,
            handle=handle,
            correction_predecessor=predecessor.identifier,
        )
        self.all_attempt_ids.add(attempt_id)

    def expected_commit(
        self,
        label: str,
        *,
        expected_head_digest: str | None,
    ) -> _ReferenceCommit:
        attempt = self.attempt(label)
        assert self.active
        assert attempt.lifecycle is _ReferenceLifecycle.PREPARED
        if self.head_digest != expected_head_digest:
            return _ReferenceCommit.STALE_HEAD
        if self.head_is_terminal and self.head_digest is not None:
            return _ReferenceCommit.TERMINAL_HEAD
        return _ReferenceCommit.PUBLISH

    def publish(self, label: str, head_digest: str) -> None:
        attempt = self.attempt(label)
        assert attempt.lifecycle is _ReferenceLifecycle.PREPARED
        attempt.lifecycle = _ReferenceLifecycle.PUBLISHED
        self.head_digest = head_digest
        self.head_is_terminal = True

    def acknowledge_effect(self, label: str, effect_name: str) -> None:
        attempt = self.attempt(label)
        assert attempt.lifecycle is _ReferenceLifecycle.PUBLISHED
        attempt.acknowledged_effects.add(effect_name)

    def finalize(self, label: str, replay_response_json: str) -> None:
        attempt = self.attempt(label)
        assert attempt.lifecycle is _ReferenceLifecycle.PUBLISHED
        assert set(_REQUIRED_FINALIZATION_EFFECTS).issubset(attempt.acknowledged_effects)
        attempt.lifecycle = _ReferenceLifecycle.RESPONSE_COMMITTED
        attempt.replay_response_json = replay_response_json


class AuditAdmissionStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        temp_root = Path(__file__).resolve().parents[2] / ".autoskillit" / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="audit-state-machine-", dir=temp_root))
        self.store_authority = _authority(self.root)
        self.ledger = DefaultAuditAdmissionLedger(self.store_authority)
        self.execution_id = RecipeExecutionId("state-machine-execution")
        self.model = _AuditReferenceModel()
        self.installation_index = 0
        self.snapshot_digest = ""
        self.version = InstallationVersion("uninitialized")
        self.requests: dict[str, AuditReservationRequest] = {}
        self.plan_set_ids: dict[str, str] = {}
        self.audit_rounds: dict[str, int] = {}
        self._activate_installation()

    def teardown(self) -> None:
        shutil.rmtree(self.root)

    def _activate_installation(self) -> None:
        self.installation_index += 1
        self.snapshot_digest = _digest(f"snapshot-{self.installation_index}")
        previous_version = self.version
        self.version = self.ledger.create_or_get_installation(
            recipe_execution_id=self.execution_id,
            snapshot_digest=self.snapshot_digest,
        )
        if previous_version.value != "uninitialized":
            assert self.version != previous_version
        self.model.begin_installation(self.version.value)
        self.requests = {}
        self.plan_set_ids = {}
        self.audit_rounds = {}
        primary = _reservation_request(
            self.root,
            self.execution_id,
            self.version,
            template=f"primary-{self.installation_index}",
            intent=f"primary-{self.installation_index}",
        )
        reserved = self.ledger.reserve(primary)
        self._register_new_slot("primary", primary, reserved)

    def _register_new_slot(
        self,
        label: str,
        request: AuditReservationRequest,
        outcome: AuditReservationOutcome,
    ) -> None:
        assert outcome.decision is ReservationDecision.DISPATCH_NEW
        assert outcome.reservation is not None
        assert outcome.reservation_handle is not None
        self.requests[label] = request
        self.plan_set_ids[label] = _plan_set_id(outcome)
        self.audit_rounds[label] = outcome.reservation.audit_round.value
        self.model.register_slot(
            label,
            outcome.attempt_id.value,
            outcome.reservation_handle,
        )
        resolved = self.ledger.resolve_reservation_handle(outcome.reservation_handle)
        assert resolved is not None
        assert resolved.current_attempt_id == outcome.attempt_id

    def _can_transition(
        self,
        label: str,
        lifecycle: _ReferenceLifecycle,
    ) -> bool:
        return (
            self.model.active
            and label in self.model.slots
            and self.model.attempt(label).lifecycle is lifecycle
        )

    def _assert_redelivery(self, label: str) -> None:
        expected = self.model.expected_redelivery(label)
        attempt = self.model.attempt(label)
        previous_handle = attempt.handle
        outcome = self.ledger.reserve(self.requests[label])
        assert outcome.decision is _PRODUCTION_DECISIONS[expected]
        if expected is _ReferenceDecision.REDISPATCH:
            assert previous_handle is not None
            assert outcome.attempt_id.value == attempt.identifier
            assert outcome.reservation_handle is not None
            assert outcome.reservation_handle != previous_handle
            assert self.ledger.resolve_reservation_handle(previous_handle) is None
            resolved = self.ledger.resolve_reservation_handle(outcome.reservation_handle)
            assert resolved is not None
            assert resolved.current_attempt_id == outcome.attempt_id
            self.model.rotate_handle(label, outcome.reservation_handle)
        elif expected is _ReferenceDecision.EXACT_REPLAY:
            assert outcome.replay_outcome is not None
            assert outcome.replay_outcome.replay_response_json == (attempt.replay_response_json)
            assert outcome.reservation_handle is None
        else:
            assert outcome.reservation_handle is None
            if expected is not _ReferenceDecision.CONFLICT:
                assert outcome.attempt_id.value == attempt.identifier

    def _prepare(self, label: str, *, accepted: bool) -> None:
        attempt = self.model.attempt(label)
        previous_handle = attempt.handle
        semantic_digest = _digest(f"semantic-{attempt.identifier}")
        prepared = self.ledger.prepare(
            AuditPrepareRequest(
                attempt_id=self._attempt_id(label),
                installation_version=self.version,
                semantic_digest=semantic_digest,
                accepted=accepted,
            )
        )
        assert prepared.accepted is accepted
        assert prepared.conflict_detail is None
        self.model.prepare(
            label,
            accepted=accepted,
            semantic_digest=semantic_digest,
        )
        assert previous_handle is not None
        assert self.ledger.resolve_reservation_handle(previous_handle) is None
        if accepted:
            repeated = self.ledger.prepare(
                AuditPrepareRequest(
                    attempt_id=self._attempt_id(label),
                    installation_version=self.version,
                    semantic_digest=semantic_digest,
                    accepted=True,
                )
            )
            assert repeated.accepted
            assert repeated.conflict_detail is None

    def _attempt_id(self, label: str):
        from autoskillit.core import AuditAttemptId

        return AuditAttemptId(self.model.attempt(label).identifier)

    def _publish(self, label: str) -> None:
        request = self.requests[label]
        expected = self.model.expected_commit(
            label,
            expected_head_digest=request.parent_authority_digest,
        )
        new_digest = _digest(
            f"head-{self.installation_index}-{label}-{self.model.attempt(label).identifier}"
        )
        new_head = _head(
            self.root,
            self.execution_id,
            self.plan_set_ids[label],
            audit_round=self.audit_rounds[label],
            current_authority_digest=new_digest,
        )
        committed = self.ledger.commit_authority(
            AuditFinalCommitRequest(
                attempt_id=self._attempt_id(label),
                installation_version=self.version,
                expected_head_digest=request.parent_authority_digest,
                new_head=new_head,
                preflight_step_names=("make-plan",),
            )
        )
        if expected is _ReferenceCommit.PUBLISH:
            assert committed.committed
            self.model.publish(label, new_digest)
        else:
            assert not committed.committed
            assert committed.conflict_detail == (
                "stale_head" if expected is _ReferenceCommit.STALE_HEAD else "terminal_head"
            )

    def _finalize(self, label: str) -> None:
        attempt_id = self._attempt_id(label)
        for effect_name in _REQUIRED_FINALIZATION_EFFECTS:
            effect_result = {"completed": True, "effect": effect_name}
            self.ledger.acknowledge_finalization_effect(
                attempt_id,
                effect_name,
                effect_result,
            )
            self.ledger.acknowledge_finalization_effect(
                attempt_id,
                effect_name,
                effect_result,
            )
            assert self.ledger.finalization_effect_result(attempt_id, effect_name) == effect_result
            self.model.acknowledge_effect(label, effect_name)
        replay_response_json = json.dumps(
            {
                "success": True,
                "audit_status": "EXACT_REPLAY",
                "audit_attempt_id": attempt_id.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=attempt_id,
            verdict=AuditVerdict.GO,
            path=self.root / f"{label}-authority.json",
            error=None,
            replay_response_json=replay_response_json,
        )
        self.ledger.finalize_response(
            attempt_id,
            outcome,
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )
        self.ledger.finalize_response(
            attempt_id,
            outcome,
            required_effect_names=_REQUIRED_FINALIZATION_EFFECTS,
        )
        self.model.finalize(label, replay_response_json)

    @precondition(
        lambda self: self._can_transition(
            "primary",
            _ReferenceLifecycle.OPEN,
        )
    )
    @rule()
    def accept_primary_semantics(self) -> None:
        self._prepare("primary", accepted=True)

    @precondition(
        lambda self: self._can_transition(
            "primary",
            _ReferenceLifecycle.OPEN,
        )
    )
    @rule()
    def reject_primary_semantics(self) -> None:
        self._prepare("primary", accepted=False)

    @precondition(
        lambda self: self._can_transition(
            "primary",
            _ReferenceLifecycle.REJECTED,
        )
    )
    @rule()
    def advance_primary_correction_once(self) -> None:
        predecessor = self._attempt_id("primary")
        corrected_request = replace(
            self.requests["primary"],
            retry_after_audit_attempt_id=predecessor,
        )
        corrected = self.ledger.reserve(corrected_request)
        assert corrected.decision is ReservationDecision.DISPATCH_NEW
        assert corrected.reservation_handle is not None
        self.model.advance_correction(
            "primary",
            corrected.attempt_id.value,
            corrected.reservation_handle,
        )
        stale = self.ledger.reserve(corrected_request)
        assert stale.decision is ReservationDecision.CONFLICT
        assert predecessor.value in self.model.consumed_correction_tokens

    @precondition(lambda self: self.model.active and "competitor" not in self.model.slots)
    @rule()
    def reserve_competing_slot(self) -> None:
        request = _reservation_request(
            self.root,
            self.execution_id,
            self.version,
            template=f"competitor-{self.installation_index}",
            intent=f"competitor-{self.installation_index}",
        )
        reserved = self.ledger.reserve(request)
        self._register_new_slot("competitor", request, reserved)

    @precondition(
        lambda self: self._can_transition(
            "competitor",
            _ReferenceLifecycle.OPEN,
        )
    )
    @rule()
    def accept_competing_semantics(self) -> None:
        self._prepare("competitor", accepted=True)

    @precondition(
        lambda self: self._can_transition(
            "primary",
            _ReferenceLifecycle.PREPARED,
        )
    )
    @rule()
    def publish_primary(self) -> None:
        self._publish("primary")

    @precondition(
        lambda self: self._can_transition(
            "competitor",
            _ReferenceLifecycle.PREPARED,
        )
    )
    @rule()
    def publish_competitor(self) -> None:
        self._publish("competitor")

    @precondition(
        lambda self: (
            self.model.active
            and any(
                attempt.lifecycle is _ReferenceLifecycle.PUBLISHED
                for attempt in self.model.slots.values()
            )
        )
    )
    @rule()
    def commit_one_published_response(self) -> None:
        label = next(
            label
            for label, attempt in sorted(self.model.slots.items())
            if attempt.lifecycle is _ReferenceLifecycle.PUBLISHED
        )
        self._finalize(label)

    @precondition(
        lambda self: (
            self.model.active
            and self.model.head_digest is not None
            and self.model.head_is_terminal
            and "successor" not in self.model.slots
        )
    )
    @rule()
    def terminal_head_rejects_explicit_successor(self) -> None:
        request = _reservation_request(
            self.root,
            self.execution_id,
            self.version,
            template=f"successor-{self.installation_index}",
            intent=f"successor-{self.installation_index}",
            parent_authority_digest=self.model.head_digest,
        )
        reserved = self.ledger.reserve(request)
        self._register_new_slot("successor", request, reserved)
        assert self.audit_rounds["successor"] == 2
        self._prepare("successor", accepted=True)
        self._publish("successor")
        assert self.model.attempt("successor").lifecycle is _ReferenceLifecycle.PREPARED

    @precondition(lambda self: self.model.active)
    @rule()
    def active_installation_is_idempotent(self) -> None:
        assert (
            self.ledger.create_or_get_installation(
                recipe_execution_id=self.execution_id,
                snapshot_digest=self.snapshot_digest,
            )
            == self.version
        )

    @precondition(lambda self: self.model.active)
    @rule()
    def clear_retires_active_installation(self) -> None:
        self.ledger.retire_installation(
            recipe_execution_id=self.execution_id,
            installation_version=self.version,
        )
        self.model.retire_installation()
        conflict = self.ledger.reserve(self.requests["primary"])
        assert conflict.decision is ReservationDecision.CONFLICT
        assert conflict.conflict_detail == "installation_retired"
        stale_prepare = self.ledger.prepare(
            AuditPrepareRequest(
                attempt_id=self._attempt_id("primary"),
                installation_version=self.version,
                semantic_digest=_digest("retired-installation"),
                accepted=True,
            )
        )
        assert not stale_prepare.accepted
        assert stale_prepare.conflict_detail == "installation_stale"

    @precondition(
        lambda self: not self.model.active and len(self.model.installation_occurrences) < 3
    )
    @rule()
    def replacement_creates_new_installation_occurrence(self) -> None:
        self._activate_installation()

    @rule()
    def restart_recovers_reference_state(self) -> None:
        restarted = DefaultAuditAdmissionLedger(self.store_authority)
        recovery = restarted.recover_all()
        assert recovery.store_health.status is AuditAdmissionStorageHealthStatus.HEALTHY
        assert {attempt.value for attempt in recovery.recovered_attempts} == (
            self.model.all_attempt_ids
        )
        assert (self.execution_id in recovery.recovered_installations) is (self.model.active)
        self.ledger = restarted
        if self.model.active:
            for attempt in self.model.slots.values():
                if attempt.lifecycle is _ReferenceLifecycle.OPEN:
                    assert attempt.handle is not None
                    resolved = self.ledger.resolve_reservation_handle(attempt.handle)
                    assert resolved is not None
                elif attempt.handle is not None:
                    assert self.ledger.resolve_reservation_handle(attempt.handle) is None

    @invariant()
    def redelivery_matches_independent_reference_model(self) -> None:
        for label in sorted(self.model.slots):
            self._assert_redelivery(label)


TestAuditAdmissionStateMachine = AuditAdmissionStateMachine.TestCase
TestAuditAdmissionStateMachine.settings = settings(
    max_examples=18,
    stateful_step_count=16,
    deadline=None,
)
