"""Pure lifecycle tests for replay-safe kitchen opening."""

from __future__ import annotations

import pytest

from autoskillit.pipeline import (
    KitchenEffectPhase,
    KitchenIntentConflict,
    KitchenOpenPhase,
    KitchenRetryDisposition,
    abort_kitchen_effect,
    advance_kitchen_phase,
    bind_kitchen_intent,
    canonical_kitchen_intent_fingerprint,
    claim_kitchen_request,
    commit_kitchen_response,
    confirm_kitchen_effect,
    mark_kitchen_effect_ambiguous,
    new_kitchen_open_state,
    release_kitchen_request,
    start_kitchen_effect,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _state():
    return new_kitchen_open_state(
        kitchen_id="kitchen-1",
        operation_id="operation-1",
        context_id="context-1",
    )


def _fingerprint(*, overrides: dict[str, str] | None = None) -> str:
    return canonical_kitchen_intent_fingerprint(
        name="implementation",
        overrides=overrides,
        ingredients_only=False,
        delivery_request=None,
        context_id="context-1",
    )


def test_operation_identity_is_distinct_from_intent_fingerprint() -> None:
    state = _state()
    fingerprint = _fingerprint()

    bound = bind_kitchen_intent(state, fingerprint=fingerprint)

    assert bound.operation_id == "operation-1"
    assert bound.kitchen_id == "kitchen-1"
    assert bound.intent_fingerprint == fingerprint
    assert fingerprint not in {bound.operation_id, bound.kitchen_id}
    assert bound.phase is KitchenOpenPhase.REQUEST_BOUND


def test_fingerprint_is_canonical_but_context_scoped() -> None:
    first = _fingerprint(overrides={"z": "2", "a": "1"})
    reordered = _fingerprint(overrides={"a": "1", "z": "2"})
    other_context = canonical_kitchen_intent_fingerprint(
        name="implementation",
        overrides={"a": "1", "z": "2"},
        ingredients_only=False,
        delivery_request=None,
        context_id="context-2",
    )

    assert first == reordered
    assert first != other_context


def test_same_operation_changed_intent_is_a_stable_conflict() -> None:
    bound = bind_kitchen_intent(_state(), fingerprint=_fingerprint())

    with pytest.raises(KitchenIntentConflict) as exc_info:
        bind_kitchen_intent(
            bound,
            fingerprint=_fingerprint(overrides={"sprint_mode": "true"}),
        )

    assert exc_info.value.state == bound


def test_started_unconfirmed_effect_requires_reconciliation() -> None:
    state = bind_kitchen_intent(_state(), fingerprint=_fingerprint())
    state = start_kitchen_effect(state, "recipe_serving")

    ambiguous = mark_kitchen_effect_ambiguous(
        state,
        "recipe_serving",
        evidence="connection lost after dispatch",
    )

    effect = next(item for item in ambiguous.effects if item.name == "recipe_serving")
    assert effect.phase is KitchenEffectPhase.AMBIGUOUS
    assert ambiguous.phase is KitchenOpenPhase.FAILED_AMBIGUOUS
    assert ambiguous.retry_disposition is KitchenRetryDisposition.RECONCILE_REQUIRED
    assert "connection lost" in ambiguous.ambiguity[0]


def test_started_effect_is_not_restarted() -> None:
    state = bind_kitchen_intent(_state(), fingerprint=_fingerprint())
    started = start_kitchen_effect(state, "recipe_serving")

    replayed = start_kitchen_effect(started, "recipe_serving")

    assert replayed == started
    assert len([effect for effect in replayed.effects if effect.name == "recipe_serving"]) == 1


def test_live_request_claim_is_exclusive_until_release() -> None:
    state = bind_kitchen_intent(_state(), fingerprint=_fingerprint())

    claimed, is_owner = claim_kitchen_request(state)
    in_progress, second_is_owner = claim_kitchen_request(claimed)
    released = release_kitchen_request(in_progress)
    reclaimed, reclaimed_is_owner = claim_kitchen_request(released)

    assert is_owner is True
    assert second_is_owner is False
    assert in_progress.retry_disposition is KitchenRetryDisposition.IN_PROGRESS
    assert released.request_active is False
    assert reclaimed_is_owner is True
    assert reclaimed.request_active is True


def test_proven_predispatch_failure_restores_retry_safe_effect() -> None:
    state = bind_kitchen_intent(_state(), fingerprint=_fingerprint())
    started = start_kitchen_effect(state, "recipe_serving")

    aborted = abort_kitchen_effect(started, "recipe_serving")
    retried = start_kitchen_effect(aborted, "recipe_serving")

    assert aborted.retry_disposition is KitchenRetryDisposition.RETRY_SAFE
    assert all(effect.name != "recipe_serving" for effect in aborted.effects)
    assert retried.retry_disposition is KitchenRetryDisposition.IN_PROGRESS
    assert next(
        effect.effect_id for effect in retried.effects if effect.name == "recipe_serving"
    ) == next(effect.effect_id for effect in started.effects if effect.name == "recipe_serving")


def test_confirmed_progress_converges_to_cached_exact_response() -> None:
    state = bind_kitchen_intent(_state(), fingerprint=_fingerprint())
    state = start_kitchen_effect(state, "recipe_serving")
    state = confirm_kitchen_effect(
        state,
        "recipe_serving",
        receipt="recipe-generation-1",
        downstream_identity="generation-1",
    )
    state = advance_kitchen_phase(state, KitchenOpenPhase.VISIBILITY_READY)

    committed = commit_kitchen_response(
        state,
        response='{"success":true,"initialization_id":"init-1"}',
        initialization_id="init-1",
    )

    assert committed.phase is KitchenOpenPhase.COMMITTED
    assert committed.cached_response == '{"success":true,"initialization_id":"init-1"}'
    assert committed.initialization_id == "init-1"
    assert committed.retry_disposition is KitchenRetryDisposition.COMMITTED_REPLAY


def test_unknown_or_out_of_order_phase_is_rejected() -> None:
    with pytest.raises(ValueError, match="illegal kitchen transition"):
        advance_kitchen_phase(_state(), KitchenOpenPhase.COMMITTED)
