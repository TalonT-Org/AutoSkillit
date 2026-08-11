"""Static general-output and recipe-delivery decision resolution."""

from __future__ import annotations

import hashlib
import json
import time

from .types._type_backend import BackendCapabilities
from .types._type_constants_registries import ANNOTATION_HARD_CAP_CHARS
from .types._type_recipe_delivery import (
    RECIPE_DELIVERY_ATTESTATION_AUDIENCE,
    HostClientAttestation,
    RecipeDeliveryAttestation,
    RecipeDeliveryBudgetDef,
    RecipeDeliveryDecision,
    RecipeDeliveryEvidenceDef,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
)


def _is_sha256_identity(value: str) -> bool:
    prefix = "sha256:"
    if not value.startswith(prefix) or len(value) != len(prefix) + 64:
        return False
    return all(character in "0123456789abcdef" for character in value[len(prefix) :])


def recipe_delivery_request_digest(request: RecipeDeliveryRequest) -> str:
    """Return the domain-labelled digest of the exact nested delivery request."""
    canonical = json.dumps(
        {
            "audience": request.audience,
            "caller_requested_outer_tokens": request.caller_requested_outer_tokens,
            "code_digest": request.code_digest,
            "contract_digest": request.contract_digest,
            "contract_version": request.contract_version,
            "delivery_call_id": request.delivery_call_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def resolve_general_output_token_limit(caps: BackendCapabilities) -> int:
    """Return the backend's conservative limit for an unnegotiated tool result."""
    return caps.unnegotiated_tool_result_token_limit


def resolve_recipe_envelope_byte_limit(capabilities: BackendCapabilities) -> int:
    """Return the conservative UTF-8 byte ceiling for an ordinary recipe envelope."""
    return resolve_general_output_token_limit(capabilities)


def resolve_recipe_section_response_bound(
    *,
    response_max_bytes: int,
    conservative_general_result_limit: int,
    page_max_bytes_override: int | None = None,
    exemption_ceiling_bytes: int | None = None,
) -> int:
    """Resolve the recipe-section byte ceiling via single-seat reconciliation.

    An override is an input to reconciliation, never a bypass — it is clamped
    to the exemption ceiling when one is provided.
    """
    if page_max_bytes_override is not None:
        candidate = page_max_bytes_override
    else:
        candidate = min(response_max_bytes, conservative_general_result_limit)
    if exemption_ceiling_bytes is not None:
        return min(candidate, exemption_ceiling_bytes)
    return candidate


def resolve_recipe_delivery_decision(
    *,
    capabilities: BackendCapabilities,
    required_serialized_tokens: int,
    budget: RecipeDeliveryBudgetDef | None,
    producer: str,
    payload_sha256: str,
    request: RecipeDeliveryRequest | None = None,
    attestation: RecipeDeliveryAttestation | None = None,
    supported_evidence: RecipeDeliveryEvidenceDef | None = None,
    now_unix: int | None = None,
    # Stage E: annotation-aware inline
    host_client_attestation: HostClientAttestation | None = None,
    payload_serialized_chars: int | None = None,
    exemption_ceiling_chars: int | None = None,
) -> RecipeDeliveryDecision:
    """Resolve one recipe response without treating caller claims as authority."""
    ordinary_limit = resolve_general_output_token_limit(capabilities)
    requested = request.caller_requested_outer_tokens if request is not None else None
    observed = (
        attestation.host_observed_requested_outer_tokens if attestation is not None else None
    )

    def _decision(
        mode: RecipeDeliveryMode,
        *,
        selected_limit: int,
        reason: str,
        receipt_status: str,
    ) -> RecipeDeliveryDecision:
        return RecipeDeliveryDecision(
            mode=mode,
            caller_requested_outer_tokens=requested,
            host_observed_requested_outer_tokens=observed,
            required_outer_tokens=required_serialized_tokens,
            unnegotiated_tool_result_token_limit=ordinary_limit,
            selected_result_token_limit=selected_limit,
            contract_digest=budget.contract_digest if budget is not None else "",
            evidence_identity=(attestation.evidence_identity if attestation is not None else None),
            reason=reason,
            producer=producer,
            payload_sha256=payload_sha256,
            receipt_status=receipt_status,
        )

    def _envelope(reason: str) -> RecipeDeliveryDecision:
        return _decision(
            RecipeDeliveryMode.ENVELOPE,
            selected_limit=ordinary_limit,
            reason=reason,
            receipt_status="not_reserved",
        )

    if required_serialized_tokens < 0:
        return _envelope("invalid_required_token_count")
    if not producer or not _is_sha256_identity(payload_sha256):
        return _envelope("invalid_payload_identity")
    # Unannotated regime: tools without annotation support are token-gated
    # at the backend's unnegotiated limit (46,500 for Claude, derived from
    # the injected 50,000-token MAX_MCP_OUTPUT_TOKENS with 7% headroom).
    # Non-AutoSkillit sessions use the client's 25,000-token default gate
    # (CLAUDE_DEFAULT_CLIENT_RESULT_TOKENS), but AutoSkillit always injects
    # CLAUDE_INJECTED_CLIENT_RESULT_TOKENS, so the operative limit is higher.
    if required_serialized_tokens <= ordinary_limit:
        return _decision(
            RecipeDeliveryMode.ORDINARY_INLINE,
            selected_limit=ordinary_limit,
            reason="fits_unnegotiated_result_limit",
            receipt_status="not_required",
        )
    # Annotation-aware inline: when the host attests annotation support
    # and the payload fits within the exemption ceiling, deliver inline
    # without the protected-delivery machinery. Scoped to backends without
    # a registered recipe_delivery_budget (i.e. Claude Code) — a backend
    # that owns a protected delivery pipeline (Codex) must always resolve
    # through that pipeline's receipt-based checks below, never through an
    # unreceipted annotation shortcut.
    if (
        host_client_attestation is not None
        and host_client_attestation.annotation_support
        and budget is None
        and payload_serialized_chars is not None
        and exemption_ceiling_chars is not None
        and exemption_ceiling_chars <= ANNOTATION_HARD_CAP_CHARS
        and payload_serialized_chars <= exemption_ceiling_chars
    ):
        return _decision(
            RecipeDeliveryMode.ORDINARY_INLINE,
            selected_limit=exemption_ceiling_chars,
            reason="annotation_aware_inline",
            receipt_status="not_required",
        )
    if not capabilities.protected_recipe_delivery_capable:
        return _envelope("protected_host_delivery_unavailable")
    if budget is None:
        return _envelope("protected_delivery_budget_unavailable")
    if not _is_sha256_identity(budget.contract_digest):
        return _envelope("invalid_contract_digest")
    if required_serialized_tokens > budget.authoritative_attested_recipe_result_token_limit:
        return _envelope("authoritative_result_limit_exceeded")
    if request is None:
        return _envelope("delivery_request_missing")
    if attestation is None:
        return _envelope("host_attestation_missing")
    if supported_evidence is None:
        return _envelope("supported_evidence_missing")
    if request.contract_version != budget.contract_version:
        return _envelope("request_contract_version_mismatch")
    if not request.delivery_call_id or not _is_sha256_identity(request.code_digest):
        return _envelope("request_identity_invalid")
    if request.audience != RECIPE_DELIVERY_ATTESTATION_AUDIENCE:
        return _envelope("request_audience_mismatch")
    if request.contract_digest != budget.contract_digest:
        return _envelope("request_contract_digest_mismatch")
    if request.caller_requested_outer_tokens != (
        budget.authoritative_attested_recipe_result_token_limit
    ):
        return _envelope("requested_result_limit_mismatch")
    if attestation.delivery_call_id != request.delivery_call_id:
        return _envelope("delivery_call_id_mismatch")
    if attestation.audience != request.audience:
        return _envelope("attestation_audience_mismatch")
    if not all(
        (
            attestation.thread_id,
            attestation.turn_id,
            attestation.outer_call_id,
            attestation.code_mode_cell_id,
            attestation.delivery_call_id,
            attestation.nonce,
        )
    ):
        return _envelope("attestation_identity_incomplete")
    if attestation.contract_version != request.contract_version:
        return _envelope("attestation_contract_version_mismatch")
    if attestation.contract_digest != request.contract_digest:
        return _envelope("attestation_contract_digest_mismatch")
    if attestation.host_observed_requested_outer_tokens != (request.caller_requested_outer_tokens):
        return _envelope("host_observation_mismatch")
    if attestation.selected_result_token_limit != (
        budget.authoritative_attested_recipe_result_token_limit
    ):
        return _envelope("host_selected_limit_mismatch")
    if attestation.code_digest != request.code_digest:
        return _envelope("code_digest_mismatch")
    if attestation.request_digest != recipe_delivery_request_digest(request):
        return _envelope("request_digest_mismatch")
    effective_now = int(time.time()) if now_unix is None else now_unix
    if attestation.expires_at_unix <= effective_now:
        return _envelope("attestation_expired")
    if attestation.parser_version != budget.parser_version:
        return _envelope("attestation_parser_version_mismatch")
    if attestation.evidence_version != budget.evidence_version:
        return _envelope("attestation_evidence_version_mismatch")
    if attestation.evidence_identity != supported_evidence.identity:
        return _envelope("unsupported_evidence_identity")
    if not all(
        (
            supported_evidence.identity,
            supported_evidence.host_channel,
            supported_evidence.cli_identity,
            supported_evidence.selected_limit_derivation,
        )
    ):
        return _envelope("supported_evidence_incomplete")
    if supported_evidence.contract_digest != budget.contract_digest:
        return _envelope("evidence_contract_digest_mismatch")
    if supported_evidence.parser_version != budget.parser_version:
        return _envelope("evidence_parser_version_mismatch")
    if supported_evidence.evidence_schema_version != budget.evidence_version:
        return _envelope("evidence_schema_version_mismatch")
    if supported_evidence.selected_result_token_limit != (
        budget.authoritative_attested_recipe_result_token_limit
    ):
        return _envelope("evidence_selected_limit_mismatch")

    return _decision(
        RecipeDeliveryMode.ATTESTED_INLINE,
        selected_limit=supported_evidence.selected_result_token_limit,
        reason="supported_host_evidence",
        receipt_status="reservation_required",
    )
