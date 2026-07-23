"""Typed recipe-delivery budget, provenance, and decision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

__all__ = [
    "RECIPE_DELIVERY_ATTESTATION_AUDIENCE",
    "RecipeDeliveryAttestation",
    "RecipeDeliveryBudgetDef",
    "RecipeDeliveryDecision",
    "RecipeDeliveryEvidenceDef",
    "RecipeDeliveryMode",
    "RecipeDeliveryRequest",
]

RECIPE_DELIVERY_ATTESTATION_AUDIENCE = "autoskillit.recipe-delivery"


class RecipeDeliveryMode(StrEnum):
    """Wire-shaping decision for one finalized recipe response."""

    ORDINARY_INLINE = "ordinary_inline"
    ATTESTED_INLINE = "attested_inline"
    ENVELOPE = "envelope"


class RecipeDeliveryBudgetDef(NamedTuple):
    """Backend-selected limits whose byte and token domains remain distinct."""

    ordinary_omitted_result_token_limit: int
    authoritative_attested_recipe_result_token_limit: int
    history_retention_token_limit: int
    measured_recipe_exemption_max_utf8_bytes: int
    headroom_tokens: int
    contract_version: int
    parser_version: int
    evidence_version: int
    contract_digest: str


class RecipeDeliveryEvidenceDef(NamedTuple):
    """Version-pinned identity of a protected host evidence channel."""

    identity: str
    host_channel: str
    evidence_schema_version: int
    parser_version: int
    cli_identity: str
    selected_limit_derivation: str
    selected_result_token_limit: int
    contract_digest: str


@dataclass(frozen=True, slots=True)
class RecipeDeliveryRequest:
    """Immutable nested request; caller fields are claims, not attestation."""

    audience: str
    delivery_call_id: str
    contract_version: int
    contract_digest: str
    caller_requested_outer_tokens: int
    code_digest: str


@dataclass(frozen=True, slots=True)
class RecipeDeliveryAttestation:
    """Host observation bound to one protected outer and nested call."""

    audience: str
    thread_id: str
    turn_id: str
    outer_call_id: str
    code_mode_cell_id: str
    delivery_call_id: str
    host_observed_requested_outer_tokens: int
    selected_result_token_limit: int
    code_digest: str
    request_digest: str
    nonce: str
    expires_at_unix: int
    contract_version: int
    contract_digest: str
    parser_version: int
    evidence_version: int
    evidence_identity: str


@dataclass(frozen=True, slots=True)
class RecipeDeliveryDecision:
    """Resolved recipe delivery outcome with explicit provenance domains."""

    mode: RecipeDeliveryMode
    caller_requested_outer_tokens: int | None
    host_observed_requested_outer_tokens: int | None
    required_outer_tokens: int
    unnegotiated_tool_result_token_limit: int
    selected_result_token_limit: int
    contract_digest: str
    evidence_identity: str | None
    reason: str
    producer: str
    payload_sha256: str
    receipt_status: str
