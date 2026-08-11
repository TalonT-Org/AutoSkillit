"""Decision-level annotation-awareness tests."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    BackendCapabilities,
    RecipeDeliveryMode,
    resolve_recipe_delivery_decision,
)
from autoskillit.core.types._type_recipe_delivery import HostClientAttestation

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
