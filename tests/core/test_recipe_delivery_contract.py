"""Typed budget, provenance, and static recipe-delivery decision contracts."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_CODEX,
    RECIPE_DELIVERY_ATTESTATION_AUDIENCE,
    BackendCapabilities,
    RecipeDeliveryAttestation,
    RecipeDeliveryBudgetDef,
    RecipeDeliveryDecision,
    RecipeDeliveryEvidenceDef,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    recipe_delivery_request_digest,
    resolve_general_output_token_limit,
    resolve_recipe_delivery_decision,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_PAYLOAD_SHA256 = f"sha256:{'a' * 64}"
_CONTRACT_DIGEST = f"sha256:{'f' * 64}"
_BUDGET = RecipeDeliveryBudgetDef(
    ordinary_omitted_result_token_limit=10_000,
    authoritative_attested_recipe_result_token_limit=56_750,
    history_retention_token_limit=56_750,
    measured_recipe_exemption_max_utf8_bytes=195_000,
    headroom_tokens=8_000,
    contract_version=1,
    parser_version=1,
    evidence_version=1,
    contract_digest=_CONTRACT_DIGEST,
)
_CODEX_CAPABILITIES = BackendCapabilities(
    unnegotiated_tool_result_token_limit=10_000,
    protected_recipe_delivery_capable=True,
    recipe_delivery_budget=_BUDGET,
)


def _request(*, requested: int | None = None) -> RecipeDeliveryRequest:
    budget = _BUDGET
    request = RecipeDeliveryRequest(
        audience=RECIPE_DELIVERY_ATTESTATION_AUDIENCE,
        delivery_call_id="delivery-1",
        contract_version=budget.contract_version,
        contract_digest=budget.contract_digest,
        caller_requested_outer_tokens=(
            budget.authoritative_attested_recipe_result_token_limit
            if requested is None
            else requested
        ),
        code_digest=f"sha256:{'b' * 64}",
    )
    return request


def _attestation(
    request: RecipeDeliveryRequest,
    *,
    observed: int | None = None,
    evidence_identity: str = "protected-test-host-v1",
) -> RecipeDeliveryAttestation:
    budget = _BUDGET
    attestation = RecipeDeliveryAttestation(
        audience=request.audience,
        thread_id="thread-1",
        turn_id="turn-1",
        outer_call_id="outer-call-1",
        code_mode_cell_id="cell-1",
        delivery_call_id=request.delivery_call_id,
        host_observed_requested_outer_tokens=(
            request.caller_requested_outer_tokens if observed is None else observed
        ),
        selected_result_token_limit=budget.authoritative_attested_recipe_result_token_limit,
        code_digest=request.code_digest,
        request_digest=recipe_delivery_request_digest(request),
        nonce="nonce-1",
        expires_at_unix=2_000_000_000,
        contract_version=request.contract_version,
        contract_digest=request.contract_digest,
        parser_version=budget.parser_version,
        evidence_version=budget.evidence_version,
        evidence_identity=evidence_identity,
    )
    return attestation


def _protected_evidence() -> RecipeDeliveryEvidenceDef:
    budget = _BUDGET
    return RecipeDeliveryEvidenceDef(
        identity="protected-test-host-v1",
        host_channel="test-only-process-isolated-host",
        evidence_schema_version=budget.evidence_version,
        parser_version=budget.parser_version,
        cli_identity="codex-test-cli",
        selected_limit_derivation="protected_resolved_outer_limit",
        selected_result_token_limit=budget.authoritative_attested_recipe_result_token_limit,
        contract_digest=budget.contract_digest,
    )


def _resolve(
    *,
    backend_name: str = "codex",
    required: int,
    request: RecipeDeliveryRequest | None = None,
    attestation: RecipeDeliveryAttestation | None = None,
    evidence: RecipeDeliveryEvidenceDef | None = None,
    budget: RecipeDeliveryBudgetDef | None = _BUDGET,
) -> RecipeDeliveryDecision:
    capabilities = (
        _CODEX_CAPABILITIES
        if backend_name == AGENT_BACKEND_CODEX
        else BackendCapabilities(unnegotiated_tool_result_token_limit=46_500)
    )
    return resolve_recipe_delivery_decision(
        capabilities=capabilities,
        required_serialized_tokens=required,
        budget=budget,
        producer="open_kitchen",
        payload_sha256=_PAYLOAD_SHA256,
        request=request,
        attestation=attestation,
        supported_evidence=evidence,
        now_unix=1_900_000_000,
    )


def test_recipe_delivery_type_fields_keep_provenance_domains_distinct() -> None:
    assert RecipeDeliveryBudgetDef._fields == (
        "ordinary_omitted_result_token_limit",
        "authoritative_attested_recipe_result_token_limit",
        "history_retention_token_limit",
        "measured_recipe_exemption_max_utf8_bytes",
        "headroom_tokens",
        "contract_version",
        "parser_version",
        "evidence_version",
        "contract_digest",
    )
    assert {field.name for field in dataclasses.fields(RecipeDeliveryRequest)} == {
        "delivery_call_id",
        "audience",
        "contract_version",
        "contract_digest",
        "caller_requested_outer_tokens",
        "code_digest",
    }
    assert {field.name for field in dataclasses.fields(RecipeDeliveryAttestation)} == {
        "audience",
        "thread_id",
        "turn_id",
        "outer_call_id",
        "code_mode_cell_id",
        "delivery_call_id",
        "host_observed_requested_outer_tokens",
        "selected_result_token_limit",
        "code_digest",
        "request_digest",
        "nonce",
        "expires_at_unix",
        "contract_version",
        "contract_digest",
        "parser_version",
        "evidence_version",
        "evidence_identity",
    }
    assert {field.name for field in dataclasses.fields(RecipeDeliveryDecision)} == {
        "mode",
        "caller_requested_outer_tokens",
        "host_observed_requested_outer_tokens",
        "required_outer_tokens",
        "unnegotiated_tool_result_token_limit",
        "selected_result_token_limit",
        "contract_digest",
        "evidence_identity",
        "reason",
        "producer",
        "payload_sha256",
        "receipt_status",
    }


@pytest.mark.parametrize(
    "contract_type",
    [RecipeDeliveryRequest, RecipeDeliveryAttestation, RecipeDeliveryDecision],
)
def test_recipe_delivery_dataclasses_have_no_defaults(
    contract_type: type[RecipeDeliveryRequest]
    | type[RecipeDeliveryAttestation]
    | type[RecipeDeliveryDecision],
) -> None:
    for field in dataclasses.fields(contract_type):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name


def test_static_budget_is_required_and_keeps_history_separate() -> None:
    budget = _BUDGET
    assert RecipeDeliveryBudgetDef._field_defaults == {}
    assert budget.ordinary_omitted_result_token_limit == 10_000
    assert budget.measured_recipe_exemption_max_utf8_bytes == 195_000
    assert (
        budget.authoritative_attested_recipe_result_token_limit
        == ((budget.measured_recipe_exemption_max_utf8_bytes + 3) // 4) + budget.headroom_tokens
    )
    assert budget.history_retention_token_limit == 56_750


def test_general_output_resolver_reads_only_backend_unnegotiated_limit() -> None:
    caps = BackendCapabilities(unnegotiated_tool_result_token_limit=321)
    assert resolve_general_output_token_limit(caps) == 321


@pytest.mark.parametrize("required", [1, 10_000])
def test_codex_ordinary_boundary_needs_no_attestation(required: int) -> None:
    decision = _resolve(required=required)
    assert decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert decision.selected_result_token_limit == 10_000
    assert decision.caller_requested_outer_tokens is None
    assert decision.host_observed_requested_outer_tokens is None


def test_exact_supported_codex_high_boundary_is_attested_inline() -> None:
    budget = _BUDGET
    request = _request()
    decision = _resolve(
        required=budget.authoritative_attested_recipe_result_token_limit,
        request=request,
        attestation=_attestation(request),
        evidence=_protected_evidence(),
    )
    assert decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    assert decision.caller_requested_outer_tokens == (
        budget.authoritative_attested_recipe_result_token_limit
    )
    assert decision.host_observed_requested_outer_tokens == (
        budget.authoritative_attested_recipe_result_token_limit
    )
    assert decision.selected_result_token_limit == (
        budget.authoritative_attested_recipe_result_token_limit
    )


def test_over_authoritative_ceiling_is_envelope() -> None:
    budget = _BUDGET
    request = _request()
    decision = _resolve(
        required=budget.authoritative_attested_recipe_result_token_limit + 1,
        request=request,
        attestation=_attestation(request),
        evidence=_protected_evidence(),
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE
    assert decision.selected_result_token_limit == budget.ordinary_omitted_result_token_limit


def test_protected_backend_without_selected_budget_fails_closed() -> None:
    decision = _resolve(required=10_001, budget=None)

    assert decision.mode is RecipeDeliveryMode.ENVELOPE
    assert decision.reason == "protected_delivery_budget_unavailable"
    assert decision.contract_digest == ""


@pytest.mark.parametrize("missing", ["request", "attestation", "evidence"])
def test_missing_authority_is_envelope(missing: str) -> None:
    request = _request()
    decision = _resolve(
        required=10_001,
        request=None if missing == "request" else request,
        attestation=None if missing == "attestation" else _attestation(request),
        evidence=None if missing == "evidence" else _protected_evidence(),
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE


def test_mismatched_observation_and_unsupported_identity_are_envelope() -> None:
    request = _request()
    mismatched = _resolve(
        required=10_001,
        request=request,
        attestation=_attestation(request, observed=10_000),
        evidence=_protected_evidence(),
    )
    unsupported = _resolve(
        required=10_001,
        request=request,
        attestation=_attestation(request, evidence_identity="unsigned-rollout"),
        evidence=_protected_evidence(),
    )
    assert mismatched.mode is RecipeDeliveryMode.ENVELOPE
    assert unsupported.mode is RecipeDeliveryMode.ENVELOPE


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_reason"),
    [
        ("selected_result_token_limit", 10_000, "host_selected_limit_mismatch"),
        ("code_digest", f"sha256:{'d' * 64}", "code_digest_mismatch"),
        ("request_digest", f"sha256:{'e' * 64}", "request_digest_mismatch"),
        ("expires_at_unix", 1_900_000_000, "attestation_expired"),
        ("nonce", "", "attestation_identity_incomplete"),
    ],
)
def test_attested_inline_requires_complete_fresh_host_binding(
    field_name: str, invalid_value: str | int, expected_reason: str
) -> None:
    request = _request()
    attestation = dataclasses.replace(
        _attestation(request),
        **{field_name: invalid_value},
    )
    decision = _resolve(
        required=10_001,
        request=request,
        attestation=attestation,
        evidence=_protected_evidence(),
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE
    assert decision.reason == expected_reason


def test_non_codex_request_cannot_upgrade_claude() -> None:
    request = _request()
    decision = _resolve(
        backend_name=AGENT_BACKEND_CLAUDE_CODE,
        required=46_501,
        request=request,
        attestation=_attestation(request),
        evidence=_protected_evidence(),
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE
    assert decision.selected_result_token_limit == 46_500


def test_history_retention_never_becomes_selected_outer_result() -> None:
    budget = _BUDGET._replace(history_retention_token_limit=999_999)
    request = _request()
    evidence = _protected_evidence()._replace(
        selected_result_token_limit=budget.authoritative_attested_recipe_result_token_limit,
    )
    decision = _resolve(
        required=10_001,
        request=request,
        attestation=_attestation(request),
        evidence=evidence,
        budget=budget,
    )
    assert decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    assert decision.selected_result_token_limit != budget.history_retention_token_limit


def test_negative_required_tokens_fail_closed() -> None:
    decision = _resolve(required=-1)
    assert decision.mode is RecipeDeliveryMode.ENVELOPE
