"""Unified recipe finalization: decision, shaping, and transactional commit."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from autoskillit._recipe_delivery_framing import (
    RECIPE_BODY_END,
    RECIPE_BODY_START,
    RECIPE_COMPLETION_SENTINEL,
)
from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    CLAUDE_CODE_CAPABILITIES,
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    BackendCapabilities,
    BoundedDeliveryRoundTripBudgetExceededError,
    FinalizedRecipeProjection,
    HostClientAttestation,
    RecipeArtifactGeneration,
    RecipeDeliveryAttestation,
    RecipeDeliveryDecision,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    RecipeExecutionId,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
    client_serialized_char_len,
    get_logger,
    resolve_general_output_token_limit,
    resolve_recipe_delivery_decision,
    resolve_recipe_envelope_byte_limit,
)
from autoskillit.execution import (
    RecipeDeliveryReceiptLedger,
    RecipeReceiptHandle,
    codex_recipe_delivery_calling_contract,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING as _RECIPE_SERVING,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    KitchenEffectPhase,
    KitchenTransitionToken,
    ReadyRecipe,
    RecipeInitializationRequirement,
    confirm_kitchen_effect,
    mark_kitchen_effect_ambiguous,
)
from autoskillit.server._recipe_artifact import (
    RecipeArtifactError,
    RecipeArtifactSchemaError,
    _qualified_sha256,
    build_canonical_recipe_artifact_payload,
    build_recipe_flow_generation,
    load_recipe_artifact,
    persist_recipe_artifact,
    prepare_recipe_delivery_generation,
    recipe_pull_producers,
    recipe_recreation_producers,
    retire_recipe_artifacts,
)
from autoskillit.server._recipe_delivery_helpers import (
    _attested_render,
    _conservative_token_upper_bound,
    _failure_decision,
    _initialization_requirements,
    _recipe_exemption_admitted_chars,
    get_context_host_client_attestation,
    initialize_host_client_attestation,
    validate_compiled_recipe_delivery_budget,
    validate_recipe_exemption_fitness,
)
from autoskillit.server._recipe_execution import install_recipe_execution, prepare_recipe_execution
from autoskillit.server._recipe_generation import (
    RecipeGenerationError,
    get_recipe_generation_store,
)
from autoskillit.server._recipe_initialization import (
    build_recipe_envelope,
    stage_recipe_initialization,
)
from autoskillit.server._recipe_section_pagination import resolve_recipe_section_bound_bytes
from autoskillit.server._response_budget import enforce_response_budget

if TYPE_CHECKING:
    from autoskillit.core import (
        RecipeDeliveryEvidenceDef,
    )
    from autoskillit.pipeline import ToolContext


def document_recipe_delivery_contract(function: Any) -> Any:
    """Append the generated Codex contract before FastMCP reads a tool docstring."""
    description = function.__doc__ or ""
    function.__doc__ = f"{description.rstrip()}\n\n{codex_recipe_delivery_calling_contract()}\n"
    return function


@dataclass(frozen=True, slots=True)
class FinalizedRecipeResponse:
    """Internal carrier consumed before FastMCP result conversion."""

    rendered: str
    decision: RecipeDeliveryDecision
    receipt_handle: RecipeReceiptHandle | None = None
    receipt_ledger: RecipeDeliveryReceiptLedger | None = None
    artifact_generation: RecipeArtifactGeneration | None = None
    finalized_projection: FinalizedRecipeProjection | None = None
    flow_generation: RecipeFlowGeneration | None = None
    execution_snapshot: RecipeExecutionSnapshot | None = None
    normalized_compile_key: str | None = None
    tool_ctx: ToolContext | None = None
    recipe_name: str | None = None
    initialization_activating: bool = False
    initialization_id: str | None = None
    initialization_requirements: tuple[RecipeInitializationRequirement, ...] = ()
    kitchen_transition_token: KitchenTransitionToken | None = None


def finalize_recipe_delivery(
    payload: dict[str, Any],
    *,
    surface: str,
    recipe_name: str,
    tool_ctx: ToolContext,
    finalized_projection: FinalizedRecipeProjection,
    flow_generation: RecipeFlowGeneration,
    canonical_artifact_payload: dict[str, Any],
    execution_snapshot: RecipeExecutionSnapshot,
    normalized_compile_key: str,
    delivery_request: RecipeDeliveryRequest | None = None,
    attestation: RecipeDeliveryAttestation | None = None,
    supported_evidence: RecipeDeliveryEvidenceDef | None = None,
    receipt_ledger: RecipeDeliveryReceiptLedger | None = None,
    now_unix: int | None = None,
    host_client_attestation: HostClientAttestation | None = None,
) -> FinalizedRecipeResponse:
    """Persist, decide, shape, and transactionally reserve one recipe response."""
    if host_client_attestation is None:
        host_client_attestation = get_context_host_client_attestation()
    surface_definition = RECIPE_DELIVERY_SURFACE_REGISTRY[surface]
    candidate_capabilities = (
        getattr(tool_ctx.backend, "capabilities", None) if tool_ctx.backend is not None else None
    )
    capabilities = (
        candidate_capabilities
        if isinstance(candidate_capabilities, BackendCapabilities)
        else replace(
            CLAUDE_CODE_CAPABILITIES,
            unnegotiated_tool_result_token_limit=(
                CLAUDE_CODE_CAPABILITIES.unnegotiated_tool_result_token_limit
            ),
            protected_recipe_delivery_capable=False,
            recipe_delivery_budget=None,
        )
    )
    delivery_budget = capabilities.recipe_delivery_budget
    ordinary_limit = resolve_general_output_token_limit(capabilities)
    envelope_byte_limit = resolve_recipe_envelope_byte_limit(capabilities)
    if (
        not isinstance(finalized_projection, FinalizedRecipeProjection)
        or not isinstance(flow_generation, RecipeFlowGeneration)
        or not isinstance(execution_snapshot, RecipeExecutionSnapshot)
        or not isinstance(normalized_compile_key, str)
        or not normalized_compile_key
    ):
        raise TypeError("finalize_recipe_delivery requires a complete prepared generation")
    candidate_payload = dict(canonical_artifact_payload)
    if (
        candidate_payload.get("flow_records") != list(flow_generation.records)
        or candidate_payload.get("recipe_flow") != flow_generation.identity()
    ):
        raise ValueError("canonical artifact payload does not match prepared flow generation")
    surface_payload = dict(payload)
    if "success" not in surface_payload:
        surface_payload["success"] = True
    for generation_field in (
        "flow_records",
        RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
        "recipe_flow",
    ):
        surface_payload[generation_field] = candidate_payload[generation_field]
    initialization_id = uuid4().hex if surface_definition.initialization_activating else None
    try:
        generation = persist_recipe_artifact(
            tool_ctx.temp_dir,
            kitchen_id=tool_ctx.kitchen_id,
            producer_tool=surface_definition.producer_tool,
            recipe_name=recipe_name,
            payload=candidate_payload,
            flow_generation=flow_generation,
        )
        get_recipe_generation_store().bind_surface(
            tool_ctx.kitchen_id,
            normalized_compile_key,
            surface,
            generation,
        )
    except (
        OSError,
        RecipeArtifactError,
        RecipeGenerationError,
        TypeError,
        ValueError,
    ):
        decision = _failure_decision(
            producer=surface_definition.producer_tool,
            reason="recipe_artifact_persistence_failed",
            selected_limit=ordinary_limit,
            contract_digest=(delivery_budget.contract_digest if delivery_budget else ""),
        )
        return FinalizedRecipeResponse(
            rendered=json.dumps(
                {"success": False, "error": "recipe_artifact_unavailable"},
                separators=(",", ":"),
            ),
            decision=decision,
        )

    surface_payload["recipe_pull"] = generation.pull_identity()
    if initialization_id is None:
        with tool_ctx.recipe_execution_lock:
            current_initialization = tool_ctx.recipe_initialization_state
        if (
            isinstance(current_initialization, InitializingRecipe)
            and current_initialization.recipe_name == recipe_name
            and current_initialization.artifact_generation.payload_sha256
            == generation.payload_sha256
            and current_initialization.artifact_generation.artifact_blob_sha256
            == generation.artifact_blob_sha256
            and current_initialization.flow_generation == flow_generation
        ):
            initialization_id = current_initialization.initialization_id
    if initialization_id is not None:
        surface_payload["initialization_id"] = initialization_id
    ordinary_rendered = json.dumps(
        surface_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    backend_name = (
        (tool_ctx.backend.name if tool_ctx.backend is not None else None)
        or capabilities.process_name
        or "unknown"
    )
    candidate_evidence = supported_evidence if surface_definition.negotiation_eligible else None
    candidate_attestation = attestation if surface_definition.negotiation_eligible else None
    candidate_request = delivery_request if surface_definition.negotiation_eligible else None
    high_rendered = (
        _attested_render(
            surface_payload,
            generation,
            budget=delivery_budget,
            evidence_identity=(
                candidate_evidence.identity if candidate_evidence is not None else "unsupported"
            ),
        )
        if delivery_budget is not None
        else ordinary_rendered
    )
    ordinary_required_tokens = _conservative_token_upper_bound(ordinary_rendered)
    required_tokens = (
        ordinary_required_tokens
        if ordinary_required_tokens <= ordinary_limit
        else _conservative_token_upper_bound(high_rendered)
    )
    decision = resolve_recipe_delivery_decision(
        capabilities=capabilities,
        required_serialized_tokens=required_tokens,
        budget=delivery_budget,
        producer=surface_definition.producer_tool,
        payload_sha256=generation.payload_sha256,
        request=candidate_request,
        attestation=candidate_attestation,
        supported_evidence=candidate_evidence,
        now_unix=now_unix,
        host_client_attestation=host_client_attestation,
        payload_serialized_chars=(
            client_serialized_char_len(ordinary_rendered).value
            if surface_definition.response_exemption is not None
            else None
        ),
        exemption_ceiling_chars=(
            _recipe_exemption_admitted_chars(surface_definition.response_exemption.max_chars)
            if surface_definition.response_exemption is not None
            else None
        ),
    )
    response_budget = tool_ctx.config.output_budget
    response_ceiling_bytes = response_budget.response_max_bytes
    page_max_bytes = response_budget.page_max_bytes
    section_response_bound_bytes = resolve_recipe_section_bound_bytes(
        response_ceiling_bytes,
        ordinary_limit,
        page_max_bytes,
        exemption_ceiling_bytes=(
            surface_definition.response_exemption.max_utf8_bytes
            if surface_definition.response_exemption is not None
            else None
        ),
    )
    if (
        decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
        and surface_definition.response_exemption_tool is None
        and len(ordinary_rendered.encode("utf-8")) > response_ceiling_bytes
    ):
        decision = replace(
            decision,
            mode=RecipeDeliveryMode.ENVELOPE,
            reason="server_response_budget_requires_envelope",
            receipt_status="not_reserved",
        )
    receipt_handle: RecipeReceiptHandle | None = None
    if decision.mode is RecipeDeliveryMode.ATTESTED_INLINE:
        if (
            delivery_budget is None
            or receipt_ledger is None
            or candidate_request is None
            or candidate_attestation is None
            or candidate_evidence is None
        ):
            decision = replace(
                decision,
                mode=RecipeDeliveryMode.ENVELOPE,
                selected_result_token_limit=ordinary_limit,
                reason="protected_receipt_store_unavailable",
                receipt_status="not_reserved",
            )
        else:
            reservation = receipt_ledger.reserve(
                capabilities=capabilities,
                required_serialized_tokens=required_tokens,
                budget=delivery_budget,
                request=candidate_request,
                attestation=candidate_attestation,
                supported_evidence=candidate_evidence,
                producer=surface_definition.producer_tool,
                payload_sha256=generation.payload_sha256,
                now_unix=int(time.time()) if now_unix is None else now_unix,
            )
            if reservation.handle is None:
                decision = replace(
                    decision,
                    mode=RecipeDeliveryMode.ENVELOPE,
                    selected_result_token_limit=ordinary_limit,
                    reason=reservation.reason,
                    receipt_status="not_reserved",
                )
            else:
                receipt_handle = reservation.handle
                decision = replace(decision, receipt_status="pending")

    initialization_requirements: tuple[RecipeInitializationRequirement, ...] = ()
    if decision.mode is RecipeDeliveryMode.ORDINARY_INLINE:
        rendered = ordinary_rendered
    elif decision.mode is RecipeDeliveryMode.ATTESTED_INLINE:
        rendered = high_rendered
    else:
        envelope_bound_bytes = envelope_byte_limit
        if surface_definition.response_exemption_tool is None:
            envelope_bound_bytes = min(envelope_bound_bytes, response_ceiling_bytes)
        try:
            initialization_requirements = _initialization_requirements(
                tool_ctx=tool_ctx,
                generation=generation,
                payload=candidate_payload,
                entrypoint=finalized_projection.entrypoint,
                bound_bytes=section_response_bound_bytes,
                initialization_id=initialization_id,
                backend_name=backend_name,
                completion_required=surface_definition.initialization_activating,
                flow_generation=flow_generation,
                execution_snapshot=execution_snapshot,
                char_ceiling=(
                    surface_definition.response_exemption.max_chars
                    if surface_definition.response_exemption is not None
                    else None
                ),
            )
            rendered = json.dumps(
                build_recipe_envelope(
                    generation=generation,
                    flow_generation=flow_generation,
                    bound_bytes=envelope_bound_bytes,
                    initialization_id=initialization_id,
                    initialization_requirements=initialization_requirements,
                    completion_required=(surface_definition.initialization_activating),
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except BoundedDeliveryRoundTripBudgetExceededError:
            raise
        except Exception:
            get_logger(__name__).error(
                "recipe initialization manifest planning failed",
                recipe_name=recipe_name,
                exc_info=True,
            )
            rendered = json.dumps(
                {"success": False, "error": "recipe_initialization_plan_failed"},
                separators=(",", ":"),
            )
    kitchen_transition_token = None
    if (
        surface.startswith("open_kitchen")
        and hasattr(tool_ctx, "kitchen_transition_lock")
        and hasattr(tool_ctx, "kitchen_open_state")
    ):
        with tool_ctx.kitchen_transition_lock:
            state = tool_ctx.kitchen_open_state
            serving_effect = next(
                (effect for effect in state.effects if effect.name == _RECIPE_SERVING),
                None,
            )
            if serving_effect is not None:
                kitchen_transition_token = KitchenTransitionToken(
                    operation_id=state.operation_id,
                    effect_id=serving_effect.effect_id,
                )
    return FinalizedRecipeResponse(
        rendered=rendered,
        decision=decision,
        receipt_handle=receipt_handle,
        receipt_ledger=receipt_ledger if receipt_handle is not None else None,
        artifact_generation=generation,
        finalized_projection=finalized_projection,
        flow_generation=flow_generation,
        execution_snapshot=execution_snapshot,
        normalized_compile_key=normalized_compile_key,
        tool_ctx=(
            tool_ctx
            if execution_snapshot is not None or kitchen_transition_token is not None
            else None
        ),
        recipe_name=recipe_name,
        initialization_activating=surface_definition.initialization_activating,
        initialization_id=initialization_id,
        initialization_requirements=initialization_requirements,
        kitchen_transition_token=kitchen_transition_token,
    )


def complete_finalized_recipe_response(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
    *,
    now_unix: int | None = None,
) -> Any:
    """Commit receipt and lifecycle state only for exact enforced response bytes."""
    handle = finalized.receipt_handle
    ledger = finalized.receipt_ledger
    parsed: dict[str, Any] | None = None
    prepared_execution: Any = None
    previous_initialization_state: Any = None
    transition_token = finalized.kitchen_transition_token
    if enforced == finalized.rendered and transition_token is not None:
        transition_owned = False
        if (
            finalized.tool_ctx is not None
            and hasattr(finalized.tool_ctx, "kitchen_transition_lock")
            and hasattr(finalized.tool_ctx, "kitchen_open_state")
        ):
            with finalized.tool_ctx.kitchen_transition_lock:
                state = finalized.tool_ctx.kitchen_open_state
                transition_owned = state.operation_id == transition_token.operation_id and any(
                    effect.name == _RECIPE_SERVING
                    and effect.effect_id == transition_token.effect_id
                    for effect in state.effects
                )
        if not transition_owned:
            enforced = json.dumps(
                {
                    "success": False,
                    "error": "kitchen_transition_ownership_mismatch",
                },
                separators=(",", ":"),
            )
    if enforced == finalized.rendered and finalized.initialization_activating:
        required_values = (
            finalized.tool_ctx,
            finalized.recipe_name,
            finalized.artifact_generation,
            finalized.flow_generation,
            finalized.execution_snapshot,
            finalized.normalized_compile_key,
            finalized.initialization_id,
        )
        if any(value is None or value == "" for value in required_values):
            enforced = json.dumps(
                {"success": False, "error": "recipe_initialization_identity_missing"},
                separators=(",", ":"),
            )
        else:
            assert finalized.tool_ctx is not None
            assert finalized.execution_snapshot is not None
            try:
                candidate = (
                    json.loads(finalized.rendered)
                    if finalized.decision.mode is not RecipeDeliveryMode.ATTESTED_INLINE
                    else {"success": True}
                )
            except json.JSONDecodeError:
                candidate = {"success": False}
            if not isinstance(candidate, dict) or candidate.get("success") is False:
                enforced = json.dumps(
                    {"success": False, "error": "recipe_initialization_failed"},
                    separators=(",", ":"),
                )
            else:
                parsed = candidate
    if enforced == finalized.rendered and finalized.initialization_activating:
        assert finalized.tool_ctx is not None
        assert finalized.recipe_name is not None
        assert finalized.artifact_generation is not None
        assert finalized.flow_generation is not None
        assert finalized.execution_snapshot is not None
        assert finalized.normalized_compile_key is not None
        assert finalized.initialization_id is not None
        assert parsed is not None
        with finalized.tool_ctx.recipe_execution_lock:
            previous_initialization_state = finalized.tool_ctx.recipe_initialization_state
        try:
            stage_recipe_initialization(
                finalized.tool_ctx,
                recipe_name=finalized.recipe_name,
                artifact_generation=finalized.artifact_generation,
                flow_generation=finalized.flow_generation,
                initialization_id=finalized.initialization_id,
                staged_snapshot=finalized.execution_snapshot,
                requirements=(
                    finalized.initialization_requirements
                    if parsed.get("delivery_bound_spill") is True
                    else ()
                ),
                generation_store_key=finalized.normalized_compile_key,
            )
            if parsed.get("delivery_bound_spill") is not True:
                prepared_execution = prepare_recipe_execution(
                    finalized.tool_ctx,
                    snapshot=finalized.execution_snapshot,
                )
                install_recipe_execution(
                    finalized.tool_ctx,
                    prepared_execution=prepared_execution,
                    completion_receipt=_qualified_sha256(
                        (
                            finalized.initialization_id
                            + finalized.artifact_generation.payload_sha256
                        ).encode("utf-8")
                    ),
                )
        except Exception:
            with finalized.tool_ctx.recipe_execution_lock:
                current_state = finalized.tool_ctx.recipe_initialization_state
                if current_state is not previous_initialization_state and isinstance(
                    current_state, InitializingRecipe
                ):
                    finalized.tool_ctx.audit_admission_ledger.retire_installation(
                        recipe_execution_id=RecipeExecutionId(
                            current_state.staged_snapshot.execution_id
                        ),
                        installation_version=current_state.installation_version,
                    )
                elif current_state is not previous_initialization_state and isinstance(
                    current_state, ReadyRecipe
                ):
                    finalized.tool_ctx.audit_admission_ledger.retire_installation(
                        recipe_execution_id=RecipeExecutionId(
                            current_state.installed_execution.snapshot.execution_id
                        ),
                        installation_version=(
                            current_state.installed_execution.installation_version
                        ),
                    )
                finalized.tool_ctx.recipe_initialization_state = previous_initialization_state
            get_logger(__name__).error(
                "recipe execution snapshot installation failed",
                initialization_id=finalized.initialization_id,
                exc_info=True,
            )
            enforced = json.dumps(
                {
                    "success": False,
                    "error": "recipe_execution_install_failed",
                },
                separators=(",", ":"),
            )
    if enforced == finalized.rendered and handle is not None:
        try:
            receipt_committed = ledger is not None and ledger.commit(
                handle,
                now_unix=int(time.time()) if now_unix is None else now_unix,
            )
        except Exception:
            receipt_committed = False
            get_logger(__name__).error(
                "recipe delivery receipt commit failed",
                exc_info=True,
            )
        if not receipt_committed:
            if finalized.initialization_activating:
                assert finalized.tool_ctx is not None
                with finalized.tool_ctx.recipe_execution_lock:
                    finalized.tool_ctx.recipe_initialization_state = previous_initialization_state
            enforced = json.dumps(
                {"success": False, "error": "recipe_delivery_receipt_commit_failed"},
                separators=(",", ":"),
            )
        else:
            handle = None
    if handle is not None and (ledger is None or not ledger.abort(handle)):
        enforced = json.dumps(
            {"success": False, "error": "recipe_delivery_receipt_abort_failed"},
            separators=(",", ":"),
        )
    _complete_kitchen_serving_transition(finalized, enforced)
    return enforced


def _complete_kitchen_serving_transition(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
) -> None:
    """Close the owned serving effect at the response-enforcement boundary."""
    transition_token = finalized.kitchen_transition_token
    tool_ctx = finalized.tool_ctx
    if transition_token is None or tool_ctx is None:
        return
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        if state.operation_id != transition_token.operation_id:
            return
        effect = next(
            (
                candidate
                for candidate in state.effects
                if candidate.name == _RECIPE_SERVING
                and candidate.effect_id == transition_token.effect_id
            ),
            None,
        )
        if effect is None or effect.phase is not KitchenEffectPhase.STARTED:
            return
        if enforced == finalized.rendered:
            state = confirm_kitchen_effect(
                state,
                effect.name,
                receipt=f"response:{effect.effect_id}",
            )
        else:
            state = mark_kitchen_effect_ambiguous(
                state,
                effect.name,
                evidence="finalized recipe response changed during enforcement",
            )
        tool_ctx.kitchen_open_state = state


def enforce_recipe_resource_response(
    finalized: FinalizedRecipeResponse,
    *,
    tool_ctx: ToolContext,
) -> str:
    """Apply the ordinary response backstop and complete the receipt transaction."""
    configured_budget = getattr(tool_ctx.config, "output_budget", None)
    output_budget = (
        configured_budget
        if isinstance(configured_budget, OutputBudgetConfig)
        else OutputBudgetConfig()
    )
    temp_dir = getattr(tool_ctx, "temp_dir", None)
    enforced = enforce_response_budget(
        finalized.rendered,
        tool_name="get_recipe",
        artifact_dir=(
            temp_dir / "responses" / "get_recipe" if isinstance(temp_dir, Path) else None
        ),
        config=output_budget,
        selected_result_token_limit=finalized.decision.selected_result_token_limit,
    )
    completed = complete_finalized_recipe_response(finalized, enforced)
    if isinstance(completed, str):
        return completed
    return json.dumps(completed, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "FinalizedRecipeResponse",
    "RECIPE_ARTIFACT_DESCRIPTOR_VERSION",
    "RECIPE_ARTIFACT_SCHEMA_VERSION",
    "RECIPE_BODY_END",
    "RECIPE_BODY_START",
    "RECIPE_COMPLETION_SENTINEL",
    "RecipeArtifactError",
    "RecipeArtifactGeneration",
    "RecipeArtifactSchemaError",
    "build_canonical_recipe_artifact_payload",
    "build_recipe_flow_generation",
    "complete_finalized_recipe_response",
    "document_recipe_delivery_contract",
    "enforce_recipe_resource_response",
    "finalize_recipe_delivery",
    "get_context_host_client_attestation",
    "initialize_host_client_attestation",
    "load_recipe_artifact",
    "persist_recipe_artifact",
    "prepare_recipe_delivery_generation",
    "recipe_pull_producers",
    "recipe_recreation_producers",
    "retire_recipe_artifacts",
    "validate_compiled_recipe_delivery_budget",
    "validate_recipe_exemption_fitness",
]
