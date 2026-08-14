"""The get_recipe_section bounded-delivery pull handler and its request state."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import cast

import structlog

from autoskillit.core import (
    CONSERVATIVE_RESULT_TOKEN_FLOOR,
    RECIPE_RESPONSE_DEFAULT_BYTES,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    BackendCapabilities,
    RecipeArtifactGeneration,
    client_serialized_char_len,
    get_logger,
    resolve_general_output_token_limit,
)
from autoskillit.pipeline import InitializingRecipe, ReadyRecipe
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_artifact import (
    RecipeStepExtractionError as _RecipeSectionError,
)
from autoskillit.server._recipe_artifact import (
    extract_step_body_from_persisted as _extract_step_body_from_persisted,
)
from autoskillit.server._recipe_delivery import (
    RecipeArtifactError,
    RecipeArtifactSchemaError,
    load_recipe_artifact,
    persist_recipe_artifact,
    recipe_pull_producers,
    recipe_recreation_producers,
)
from autoskillit.server._recipe_generation import (
    get_recipe_generation_store,
    thaw_recipe_generation_mapping,
)
from autoskillit.server._recipe_initialization import (
    FinalizedRecipeSectionResponse,
    matches_recipe_initialization_requirement,
    recipe_initialization_progress_counts,
    replay_terminal_section_response,
)
from autoskillit.server._recipe_section_pagination import (
    RecipeSectionBoundError,
    RecipeSectionNonConvergenceError,
    RecipeSectionPaginationError,
    RecipeSectionRequestState,
    get_or_build_recipe_section_page_plan,
    recipe_section_continuation_binding,
    render_recipe_section_failure,
    render_recipe_section_page,
    resolve_recipe_section_bound_bytes,
    select_recipe_section,
)
from autoskillit.server._state import _get_ctx_or_none
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._serve_helpers import response_backstop_tool_meta

logger = get_logger(__name__)


def _inject_initialization_counters(
    rendered: str,
    *,
    completed_parts: int,
    total_parts: int,
    remaining_section_pulls: int,
) -> str:
    """Replace top-level initialization counter values in a rendered page."""
    replacements = {
        "completed_parts": completed_parts,
        "total_parts": total_parts,
        "remaining_section_pulls": remaining_section_pulls,
    }
    page = json.loads(rendered)
    if not isinstance(page, dict):
        raise TypeError("rendered recipe section page must be a JSON object")
    page.update(replacements)
    return json.dumps(page, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


_RECIPE_SECTION_REQUEST_STATE: ContextVar[RecipeSectionRequestState] = ContextVar(
    "recipe_section_request_state"
)


def _recipe_section_request_state_factory() -> RecipeSectionRequestState:
    tool_ctx = _get_ctx_or_none()
    admitted = tool_ctx is not None and tool_ctx.recipes is not None
    response_max_bytes = RECIPE_RESPONSE_DEFAULT_BYTES
    conservative_limit = CONSERVATIVE_RESULT_TOKEN_FLOOR
    page_max_bytes: int | None = None
    if tool_ctx is not None:
        configured_response_max = tool_ctx.config.output_budget.response_max_bytes
        if isinstance(configured_response_max, int) and configured_response_max > 0:
            response_max_bytes = configured_response_max
        configured_page_max = tool_ctx.config.output_budget.page_max_bytes
        if isinstance(configured_page_max, int) and configured_page_max > 0:
            page_max_bytes = configured_page_max
        backend_capabilities = (
            tool_ctx.backend.capabilities
            if tool_ctx.backend is not None
            and isinstance(
                getattr(tool_ctx.backend, "capabilities", None),
                BackendCapabilities,
            )
            else None
        )
        if backend_capabilities is not None:
            conservative_limit = resolve_general_output_token_limit(backend_capabilities)
    _exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["get_recipe_section"]
    return RecipeSectionRequestState(
        admitted=admitted,
        recipe_section_bound_bytes=resolve_recipe_section_bound_bytes(
            response_max_bytes,
            conservative_limit,
            page_max_bytes,
            exemption_ceiling_bytes=_exemption.max_utf8_bytes,
        ),
        recipe_section_bound_chars=_exemption.max_chars,
    )


def _current_recipe_section_request_state() -> RecipeSectionRequestState:
    return _RECIPE_SECTION_REQUEST_STATE.get()


def _recipe_section_cancellation_response(
    state: RecipeSectionRequestState,
    _error: asyncio.CancelledError,
) -> str:
    return render_recipe_section_failure(
        "recipe_section_cancelled",
        bound_bytes=state.recipe_section_bound_bytes,
        context={"admitted": state.admitted},
    )


def _recipe_section_failure(
    code: str,
    *,
    context: Mapping[str, object] | None = None,
) -> str:
    state = _current_recipe_section_request_state()
    return render_recipe_section_failure(
        code,
        bound_bytes=state.recipe_section_bound_bytes,
        context=context,
    )


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core"},
    annotations={"readOnlyHint": True},
    meta=response_backstop_tool_meta("get_recipe_section"),
)
@track_response_size("get_recipe_section")
@_cancellation_shield(
    state_factory=_recipe_section_request_state_factory,
    state_context_var=_RECIPE_SECTION_REQUEST_STATE,
    response_factory=_recipe_section_cancellation_response,
)
async def get_recipe_section(
    section: str,
    recipe_name: str,
    producer_tool: str,
    descriptor_version: int,
    schema_version: int,
    payload_sha256: str,
    artifact_blob_sha256: str,
    artifact_blob_size_bytes: int,
    body_sha256: str,
    body_size_bytes: int,
    flow_schema_version: int,
    flow_sha256: str,
    flow_size_bytes: int,
    flow_record_count: int,
    part: int = 0,
    initialization_id: str | None = None,
    page_plan_sha256: str | None = None,
    continuation: str | None = None,
) -> str:
    """Retrieve a recipe step or section from the persisted recipe artifact.

    Fixed sections are ``content``, ``ingredients_table``,
    ``orchestration_rules``, ``stop_step_semantics``, ``errors``, and
    ``warnings``. A validated ``post_prune_step_names`` entry selects raw
    named-step YAML. Every page carries ``pagination_version``,
    ``section_registry_sha256``, section/plan digests, and immutable payload
    and body identities. Consumers must reject unknown versions or formats.

    ``content_format`` selects exactly one reconstruction algorithm:
    ``raw-text`` concatenates contiguous UTF-8 byte ranges;
    ``json-scalar-page`` JSON-decodes and concatenates string pages;
    ``json-array-page`` JSON-decodes and extends complete array pages; and
    ``json-element-fragment`` JSON-decodes string fragments, concatenates and
    verifies one canonical element, then JSON-decodes that element. Arrays may
    interleave complete pages and oversized-element fragments.

    Args:
        section: The step or section name to retrieve. Must match a
            ``post_prune_step_names`` entry from the envelope, or the
            fixed section names documented above.
        part: Continuation index (0-based). Default 0 returns the first
            chunk; pass the value from the previous response's
            ``next_part`` to retrieve the next chunk.
        recipe_name: Recipe identity copied from the envelope's
            ``recipe_pull.recipe_name`` field.
        producer_tool: Producer identity copied from the envelope's
            ``recipe_pull.producer_tool`` field.
        payload_sha256: Domain-labelled semantic payload identity.
        artifact_blob_sha256: Digest of the exact persisted blob bytes.
        artifact_blob_size_bytes: Exact persisted blob byte size.
        body_sha256: Digest of the recipe body bytes.
        body_size_bytes: Exact recipe body byte size.
        flow_schema_version: Exact flow-record schema version.
        flow_sha256: Digest of the complete ordered flow generation.
        flow_size_bytes: Exact length-prefixed flow-generation byte size.
        flow_record_count: Exact number of canonical flow records.

    Returns:
        A versioned JSON page. Nonterminal pages include ``next_part``;
        terminal pages omit it.

    This tool requires the kitchen to be open (gated by open_kitchen).

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        request_state = _current_recipe_section_request_state()
        with structlog.contextvars.bound_contextvars(tool="get_recipe_section"):
            tool_ctx = _get_ctx_or_none()
            if tool_ctx is None or tool_ctx.recipes is None:
                return json.dumps({"success": False, "error": "kitchen not open"})

            if not recipe_name or not producer_tool:
                return _recipe_section_failure("recipe_artifact_identity_required")
            requested_recipe_name = recipe_name

            if producer_tool not in recipe_pull_producers():
                return _recipe_section_failure("invalid_recipe_artifact_identity")

            artifact_dir = getattr(tool_ctx, "temp_dir", None)
            if not isinstance(artifact_dir, Path):
                return _recipe_section_failure("invalid_recipe_artifact_identity")
            try:
                identity = RecipeArtifactGeneration(
                    producer_tool=producer_tool,
                    recipe_name=requested_recipe_name,
                    descriptor_version=descriptor_version,
                    schema_version=schema_version,
                    payload_sha256=payload_sha256,
                    artifact_blob_sha256=artifact_blob_sha256,
                    artifact_blob_size_bytes=artifact_blob_size_bytes,
                    body_sha256=body_sha256,
                    body_size_bytes=body_size_bytes,
                    flow_schema_version=flow_schema_version,
                    flow_sha256=flow_sha256,
                    flow_size_bytes=flow_size_bytes,
                    flow_record_count=flow_record_count,
                )
            except (TypeError, ValueError):
                return _recipe_section_failure("invalid_recipe_artifact_identity")
            if not identity.has_valid_read_bounds():
                return _recipe_section_failure("invalid_recipe_artifact_identity")
            if part < 0:
                return _recipe_section_failure("invalid_recipe_section_part")

            try:
                persisted = load_recipe_artifact(
                    artifact_dir,
                    kitchen_id=tool_ctx.kitchen_id,
                    identity=identity,
                )
            except RecipeArtifactSchemaError as exc:
                logger.warning(
                    "get_recipe_section_schema_mismatch",
                    stage="load",
                    detail=str(exc),
                )
                return _recipe_section_failure("recipe_artifact_schema_mismatch")
            except RecipeArtifactError:
                if producer_tool not in recipe_recreation_producers():
                    return _recipe_section_failure("recipe_artifact_unavailable")
                generation_record = get_recipe_generation_store().lookup_artifact(
                    tool_ctx.kitchen_id,
                    identity,
                )
                if generation_record is None:
                    return _recipe_section_failure("invalid_recipe_artifact_identity")
                try:
                    recreated_payload = thaw_recipe_generation_mapping(
                        generation_record.artifact_payload
                    )
                    recreated_generation = persist_recipe_artifact(
                        artifact_dir,
                        kitchen_id=tool_ctx.kitchen_id,
                        producer_tool=producer_tool,
                        recipe_name=requested_recipe_name,
                        payload=recreated_payload,
                        flow_generation=generation_record.flow_generation,
                    )
                except RecipeArtifactSchemaError as exc:
                    logger.warning(
                        "get_recipe_section_schema_mismatch",
                        stage="recreate_persist",
                        detail=str(exc),
                    )
                    return _recipe_section_failure("recipe_artifact_schema_mismatch")
                except (OSError, RecipeArtifactError, TypeError):
                    return _recipe_section_failure(
                        "recipe_artifact_unavailable",
                        context={"detail": "recreation write failed"},
                    )
                if recreated_generation != identity:
                    return _recipe_section_failure("invalid_recipe_artifact_identity")

                try:
                    persisted = load_recipe_artifact(
                        artifact_dir,
                        kitchen_id=tool_ctx.kitchen_id,
                        identity=identity,
                    )
                except RecipeArtifactSchemaError as exc:
                    logger.warning(
                        "get_recipe_section_schema_mismatch",
                        stage="reload",
                        detail=str(exc),
                    )
                    return _recipe_section_failure("recipe_artifact_schema_mismatch")
                except RecipeArtifactError as exc:
                    logger.warning(
                        "get_recipe_section_artifact_unavailable",
                        stage="reload",
                        detail=str(exc),
                        exc_info=True,
                    )
                    return _recipe_section_failure(
                        "recipe_artifact_unavailable",
                        context={"detail": "post-recreation reload failed"},
                    )

            try:
                selected = select_recipe_section(
                    persisted,
                    section,
                    dynamic_content_loader=lambda step_name: _extract_step_body_from_persisted(
                        persisted, step_name
                    ),
                )
            except _RecipeSectionError as exc:
                return _recipe_section_failure(exc.code)
            selected = replace(selected, initialization_id=initialization_id)
            if not selected.present:
                return _recipe_section_failure(
                    "section_not_found",
                    context={"section": section},
                )

            try:
                page_plan = get_or_build_recipe_section_page_plan(
                    kitchen_id=tool_ctx.kitchen_id,
                    generation=identity,
                    selected=selected,
                    recipe_section_bound_bytes=request_state.recipe_section_bound_bytes,
                    char_ceiling=RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[
                        "get_recipe_section"
                    ].max_chars,
                )
            except RecipeSectionBoundError:
                return _recipe_section_failure("recipe_section_bound_too_small")
            except RecipeSectionNonConvergenceError:
                return _recipe_section_failure("recipe_section_pagination_nonconvergent")
            except RecipeSectionPaginationError:
                logger.error("get_recipe_section pagination invariant failure", exc_info=True)
                return _recipe_section_failure("recipe_section_internal_error")
            if part >= page_plan.total_parts:
                return _recipe_section_failure(
                    "invalid_recipe_section_part",
                    context={"total_parts": page_plan.total_parts},
                )
            if page_plan_sha256 is not None and (page_plan_sha256 != page_plan.page_plan_sha256):
                return _recipe_section_failure("invalid_recipe_page_plan_identity")
            active_initialization: InitializingRecipe | ReadyRecipe | None = None
            if initialization_id is not None:
                with tool_ctx.recipe_execution_lock:
                    state = tool_ctx.recipe_initialization_state
                if not matches_recipe_initialization_requirement(
                    state,
                    initialization_id=initialization_id,
                    artifact_generation=identity,
                    section=section,
                    page_plan_sha256=page_plan.page_plan_sha256,
                ):
                    return _recipe_section_failure("invalid_recipe_initialization_identity")
                assert isinstance(state, (InitializingRecipe, ReadyRecipe))
                active_initialization = state
            expected_continuation = (
                None
                if part == 0
                else recipe_section_continuation_binding(
                    generation=identity,
                    initialization_id=initialization_id,
                    section=section,
                    section_sha256=page_plan.manifest.section_sha256,
                    page_plan_sha256=page_plan.page_plan_sha256,
                    next_part=part,
                )
            )
            if continuation != expected_continuation:
                return _recipe_section_failure("invalid_recipe_section_continuation")
            rendered = render_recipe_section_page(page_plan, part)
            if isinstance(active_initialization, InitializingRecipe):
                completed_parts, total_parts, remaining_section_pulls = (
                    recipe_initialization_progress_counts(
                        active_initialization,
                        section=section,
                        page_plan_sha256=page_plan.page_plan_sha256,
                        part=part,
                    )
                )
                rendered = _inject_initialization_counters(
                    rendered,
                    completed_parts=completed_parts,
                    total_parts=total_parts,
                    remaining_section_pulls=remaining_section_pulls,
                )
                if len(rendered.encode("utf-8")) > request_state.recipe_section_bound_bytes:
                    return _recipe_section_failure("recipe_section_bound_too_small")
                # Final-form character validation: the post-mutation rendered
                # string is the actual delivered form; check it against the
                # independent serialized-character ceiling.
                if (
                    request_state.recipe_section_bound_chars is not None
                    and client_serialized_char_len(rendered).value
                    > request_state.recipe_section_bound_chars
                ):
                    return _recipe_section_failure("recipe_section_bound_too_small")
            # Extract terminal-page metadata from the page plan descriptor
            # (no content parsing needed — these are plan-level values).
            terminal = part + 1 == page_plan.total_parts
            page_descriptor = page_plan.manifest.pages[part]
            content_sha256: str | None = page_descriptor.page_content_sha256 if terminal else None
            if isinstance(active_initialization, ReadyRecipe):
                if isinstance(content_sha256, str):
                    replayed = replay_terminal_section_response(
                        tool_ctx,
                        initialization_id=active_initialization.initialization_id,
                        section=section,
                        part=part,
                        content_sha256=content_sha256,
                    )
                    if replayed is not None:
                        return replayed
                return rendered
            if active_initialization is None:
                return rendered
            completion_receipt: str | None = None
            if terminal:
                rendered_payload = json.loads(rendered)
                completion_receipt = rendered_payload.get("completion_receipt")
            return cast(
                str,
                FinalizedRecipeSectionResponse(
                    rendered=rendered,
                    tool_ctx=tool_ctx,
                    initialization_id=active_initialization.initialization_id,
                    artifact_generation=identity,
                    section=section,
                    page_plan_sha256=page_plan.page_plan_sha256,
                    part=part,
                    content_sha256=(content_sha256 if isinstance(content_sha256, str) else ""),
                    completion_receipt=(
                        completion_receipt if isinstance(completion_receipt, str) else None
                    ),
                ),
            )
    except Exception:
        logger.error("get_recipe_section unhandled exception", exc_info=True)
        return _recipe_section_failure("recipe_section_internal_error")


__all__ = [
    "_RECIPE_SECTION_REQUEST_STATE",
    "_RecipeSectionError",
    "_current_recipe_section_request_state",
    "_extract_step_body_from_persisted",
    "_inject_initialization_counters",
    "_recipe_section_cancellation_response",
    "_recipe_section_failure",
    "_recipe_section_request_state_factory",
    "get_recipe_section",
    "logger",
]
