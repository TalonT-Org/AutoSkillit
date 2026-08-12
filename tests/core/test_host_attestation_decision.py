"""Decision-level annotation-awareness tests."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    BackendCapabilities,
    RecipeDeliveryMode,
    resolve_recipe_delivery_decision,
)
from autoskillit.core.types._type_recipe_delivery import (
    HostClientAttestation,
    RecipeDeliveryBudgetDef,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_PAYLOAD_SHA = "sha256:" + "a" * 64


def test_annotation_aware_inline_when_attested_and_fits() -> None:
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=150_000,
        exemption_ceiling_chars=195_000,
    )
    assert decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert decision.reason == "annotation_aware_inline"


def test_no_attestation_falls_through_to_envelope() -> None:
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE


def test_attestation_without_annotation_support_falls_through() -> None:
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=False,
        ),
        payload_serialized_chars=150_000,
        exemption_ceiling_chars=195_000,
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE


def test_payload_exceeding_ceiling_falls_through() -> None:
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=200_000,
        exemption_ceiling_chars=195_000,
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE


_PROTECTED_BUDGET = RecipeDeliveryBudgetDef(
    ordinary_omitted_result_token_limit=10_000,
    authoritative_attested_recipe_result_token_limit=100_000,
    history_retention_token_limit=50_000,
    measured_recipe_exemption_max_utf8_bytes=195_000,
    headroom_tokens=1_000,
    contract_version=1,
    parser_version=1,
    evidence_version=1,
    contract_digest="sha256:" + "c" * 64,
)


def test_protected_backend_never_uses_annotation_aware_inline() -> None:
    """A backend with its own recipe_delivery_budget (Codex) must never bypass
    its receipt-based protected delivery pipeline via the annotation-aware
    shortcut, even when a (misappropriated) host client attestation claims
    annotation support and the payload fits the exemption ceiling.
    """
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=_PROTECTED_BUDGET,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=150_000,
        exemption_ceiling_chars=195_000,
    )
    assert decision.reason != "annotation_aware_inline"
    assert decision.mode is not RecipeDeliveryMode.ORDINARY_INLINE


def test_annotated_regime_is_char_gated_independently() -> None:
    """Annotated regime: resolved page char ceiling ≤ entry max_chars
    ≤ ANNOTATION_HARD_CAP_CHARS (500,000). The annotation replaces the
    token threshold for that tool — no cross-unit comparison.
    """
    from autoskillit.core import ANNOTATION_HARD_CAP_CHARS

    # Annotated: payload fits char ceiling → inline
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,  # exceeds token limit
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=190_000,
        exemption_ceiling_chars=195_000,
    )
    assert decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert decision.reason == "annotation_aware_inline"
    # Char ceiling must be within the hard cap
    assert 195_000 <= ANNOTATION_HARD_CAP_CHARS


def test_unannotated_regime_is_token_gated() -> None:
    """Unannotated regime: estimated result tokens ≤ the backend's
    unnegotiated token limit. The token gate is independent of char ceiling.
    """
    # Small payload fitting the token limit → inline regardless of attestation
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=30_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
    )
    assert decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert decision.reason == "fits_unnegotiated_result_limit"
    assert decision.selected_result_token_limit == 46_500

    # Large payload exceeding token limit but no attestation → envelope
    decision2 = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
    )
    assert decision2.mode is RecipeDeliveryMode.ENVELOPE


def test_regimes_are_independent_no_cross_unit_comparison() -> None:
    """The two regimes must be independent: changing char-domain inputs must
    not alter the unannotated token-domain outcome, and changing token-domain
    inputs must not alter the annotated char-domain outcome.

    Independence invariant:
    - Annotated regime: decision depends only on (attestation, payload_chars,
      ceiling_chars, ANNOTATION_HARD_CAP_CHARS, budget).
    - Unannotated regime: decision depends only on (required_tokens,
      unnegotiated_limit).
    """
    # 1. Unannotated regime: varying char inputs has no effect
    base_unannotated = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=30_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        # No attestation, no char inputs
    )
    assert base_unannotated.reason == "fits_unnegotiated_result_limit"

    # Adding char-domain inputs without attestation: outcome unchanged
    with_chars = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=30_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        payload_serialized_chars=190_000,
        exemption_ceiling_chars=195_000,
    )
    assert with_chars.reason == base_unannotated.reason
    assert with_chars.mode is base_unannotated.mode

    # 2. Annotated regime: varying token limit has no effect when payload
    # exceeds the token limit but fits the char ceiling
    annotated_small_limit = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=5_000),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=150_000,
        exemption_ceiling_chars=195_000,
    )
    annotated_large_limit = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=99_000),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=150_000,
        exemption_ceiling_chars=195_000,
    )
    # Both resolve to annotation_aware_inline: the token limit is irrelevant
    # when the payload exceeds it and attestation + char ceiling qualify
    assert annotated_small_limit.reason == "annotation_aware_inline"
    assert annotated_large_limit.reason == "annotation_aware_inline"


def test_exemption_ceiling_above_hard_cap_falls_through() -> None:
    """The annotation-aware branch must not fire when the registered exemption
    ceiling itself exceeds ANNOTATION_HARD_CAP_CHARS, regardless of how small
    the actual payload is — the hard cap bounds the ceiling, not just the
    payload."""
    decision = resolve_recipe_delivery_decision(
        capabilities=BackendCapabilities(unnegotiated_tool_result_token_limit=46_500),
        required_serialized_tokens=100_000,
        budget=None,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA,
        host_client_attestation=HostClientAttestation(
            attested_client_gate_tokens=50_000,
            annotation_support=True,
        ),
        payload_serialized_chars=1_000,
        exemption_ceiling_chars=500_001,
    )
    assert decision.reason != "annotation_aware_inline"
    assert decision.mode is not RecipeDeliveryMode.ORDINARY_INLINE
