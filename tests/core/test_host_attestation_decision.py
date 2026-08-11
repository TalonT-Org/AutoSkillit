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
