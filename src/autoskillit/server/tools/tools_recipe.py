"""MCP tool handlers: load_recipe, list_recipes, validate_recipe, migrate_recipe."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.config import (
    build_config_authoritative_layer,
    build_config_default_layer,
    resolve_ingredient_defaults,
)
from autoskillit.core import (
    BackendCapabilities,
    RecipeArtifactGeneration,
    RecipeDeliveryRequest,
    RecipeLoadError,
    fast_dumps,
    get_logger,
    load_yaml,
    resolve_general_output_token_limit,
    temp_dir_display_str,
)  # noqa: F401
from autoskillit.pipeline import (  # noqa: F401
    GATED_TOOLS,
    UNGATED_TOOLS,
    InitializingRecipe,
    ReadyRecipe,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import (
    _apply_triage_gate,
    resolve_log_dir,
    strip_ingredients_only_keys,
)
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._recipe_delivery import (
    RecipeArtifactError,
    RecipeArtifactSchemaError,
    document_recipe_delivery_contract,
    finalize_recipe_delivery,
    load_recipe_artifact,
    persist_recipe_artifact,
    prepare_recipe_delivery_generation,
    recipe_pull_producers,
    recipe_recreation_producers,
)
from autoskillit.server._recipe_generation import (
    get_recipe_generation_store,
    thaw_recipe_generation_mapping,
)
from autoskillit.server._recipe_initialization import (
    FinalizedRecipeSectionResponse,
    build_completion_response,
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
from autoskillit.server.tools._authority_feedback import build_authority_clobber_warnings
from autoskillit.server.tools._auto_overrides import (
    _compute_effective_backend_map,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._serve_helpers import (
    _admit_recipe_name,
    build_backend_capabilities_map,
    pop_finalized_recipe_projection,
    render_served_response,
    response_backstop_tool_meta,
    serve_recipe,
)
from autoskillit.server.tools._types import _validate_result

logger = get_logger(__name__)


class _RecipeSectionError(Exception):
    """Structured artifact failure surfaced by ``get_recipe_section``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


_RECIPE_SECTION_REQUEST_STATE: ContextVar[RecipeSectionRequestState] = ContextVar(
    "recipe_section_request_state"
)


def _recipe_section_request_state_factory() -> RecipeSectionRequestState:
    tool_ctx = _get_ctx_or_none()
    admitted = tool_ctx is not None and tool_ctx.recipes is not None
    response_max_bytes = 90_000
    conservative_limit = 10_000
    page_max_bytes: int | None = None
    if tool_ctx is not None:
        configured_response_max = getattr(
            getattr(tool_ctx.config, "output_budget", None),
            "response_max_bytes",
            None,
        )
        if isinstance(configured_response_max, int) and configured_response_max > 0:
            response_max_bytes = configured_response_max
        configured_page_max = getattr(
            getattr(tool_ctx.config, "output_budget", None), "page_max_bytes", None
        )
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
    return RecipeSectionRequestState(
        admitted=admitted,
        recipe_section_bound_bytes=resolve_recipe_section_bound_bytes(
            response_max_bytes,
            conservative_limit,
            page_max_bytes,
        ),
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
    tags={"autoskillit", "kitchen-core", "fleet-dispatch"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("list_recipes")
async def list_recipes() -> str:
    """List available recipes from .autoskillit/recipes/.

    Returns a JSON array of recipes with name, description, and summary.
    Recipes are YAML workflow definitions that agents follow as orchestration
    instructions. Use load_recipe to load a specific recipe.
    To create a new recipe, use the /write-recipe skill.
    To generate recipes as part of project onboarding, use /setup-project.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_recipe and executed
    step-by-step by the agent. Recipes live in .autoskillit/recipes/ (NOT in
    .autoskillit/skills/ or any other directory).

    This tool requires the kitchen to be open (gated by open_kitchen).

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="list_recipes"):
            tool_ctx = _get_ctx_or_none()
            if tool_ctx is None or tool_ctx.recipes is None:
                return json.dumps({"error": "kitchen not open — call open_kitchen first"})
            result = tool_ctx.recipes.list_all(
                tool_ctx.project_dir, features=tool_ctx.config.features
            )
            return json.dumps(result)
    except Exception:
        logger.error("list_recipes unhandled exception", exc_info=True)
        return json.dumps({"error": "internal error listing recipes — see server logs"})


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet-dispatch"},
    annotations={"readOnlyHint": True},
    meta=response_backstop_tool_meta("load_recipe"),
)
@document_recipe_delivery_contract
@_cancellation_shield()
@track_response_size("load_recipe")
async def load_recipe(
    name: str,
    overrides: dict[str, str] | None = None,
    ingredients_only: bool = False,
    delivery_request: RecipeDeliveryRequest | None = None,
) -> str:
    """Load a recipe by name and return its raw YAML content.

    The YAML follows the recipe schema (ingredients, steps with tool/action,
    on_success/on_failure routing, retry blocks). The agent should interpret
    the YAML and execute the steps using the appropriate MCP tools.

    CRITICAL — PIPELINE DISCIPLINE:
    NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash,
    Agent, WebFetch, WebSearch, NotebookEdit) during pipeline execution.
    All investigation and code changes happen inside headless sessions
    launched by run_skill. Shell commands use run_cmd.
    The task description is INPUT to the recipe steps — pass it through
    as an ingredient value, do not act on it yourself.

    After collecting ingredient values from the user, IMMEDIATELY proceed
    to the first recipe step. Do not investigate, research, or explore the
    task — the recipe steps handle all investigation through delegated sessions.

    After loading:
    1. If `diagram` is not None: show the `diagram` field content to the user directly.
    2. If `diagram` is None: run `autoskillit recipes render {name}` to generate the
       diagram, or invoke the /render-recipe skill. The canonical visual grammar is
       defined in the render-recipe SKILL.md — do not attempt to render inline.
       (See: .claude/skills/render-recipe/SKILL.md)
    3. If the user requests changes, use the /write-recipe skill
       to apply modifications. That skill has the complete schema, validation rules,
       and formatting constraints needed for correct changes. Do NOT edit the YAML
       file directly — always delegate modifications to write-recipe.
    4. Collect recipe ingredients from the user:
       Collect ingredient values conversationally:
       a. Ask the user a single open-ended question — what would they like to do?
          Do NOT prompt for each ingredient field individually.
       b. From the user's free-form response, infer as many ingredient values
          as possible (e.g. task description, source directory, run name).
       c. If any required ingredients could not be inferred, ask one
          follow-up question covering only those missing required values.
       d. Accept optional ingredients at their default values unless the
          user explicitly mentioned an override in their response.
    5. Execute the pipeline steps by calling MCP tools directly

    Allowed during pipeline execution:
    - AutoSkillit MCP tools (call directly, not via subagents)
    - AskUserQuestion (user interaction)
    - Steps with `capture:` fields extract values from tool results into a
      pipeline context dict. Use captured values in subsequent steps via
      ${{ context.var_name }} in `with:` arguments.
    - Thread outputs from each step into the next (e.g. worktree_path from
      implement into test_check).
    - Steps with a `model:` field: when calling `run_skill`,
      pass the step's `model` value as the `model` parameter to the tool.

    TOKEN USAGE TRACKING:
    - BEFORE executing the pipeline, call kitchen_status() and read
      token_usage_verbosity. This controls how you handle token reporting:
        "summary" → the open_pr skill self-retrieves its own
                     token summary from disk (pipeline-scoped). Do NOT call
                     get_token_summary for this purpose and do NOT pre-stage
                     <temp_dir>/open-pr/token_summary.md — the skill handles it.
        "none"    → do NOT call get_token_summary. Skip token reporting entirely.
    - Do NOT print or render a token usage table after individual steps.
      Only one call to get_token_summary is permitted per pipeline run,
      at the very end. Intermediate rendering is prohibited.
    - Pass step_name (the YAML step key, e.g. "implement") in the with: block
      when calling run_skill. The server accumulates token
      usage server-side, grouped by step name.
    - The step_name value MUST match the YAML step key exactly — do NOT append
      clone instance numbers, retry counts, or any disambiguation suffixes.
      Parallel runs of the same step across multiple clones all use the same
      canonical step_name; the token log aggregates them automatically.
    - Non-skill steps (test_check, run_cmd, merge_worktree) have no token usage —
      they are not included in get_token_summary output. Do not add rows for them.

    STEP TIMING:
    - All recipe-step tools (run_skill, run_cmd, test_check, merge_worktree,
      classify_fix, clone_repo, remove_clone, push_to_remote, reset_test_dir)
      accept a step_name parameter. Pass the YAML step key in each with: block.
    - Timing data is included as a column in the token summary table when
      format=table is used. No separate timing file is needed.
    - Non-skill steps that lack step_name values are not included in get_timing_summary.

    ROUTING RULES — MANDATORY:
    - When a tool returns a failure result, you MUST follow the step's on_failure route.
    - When a step fails, route to on_failure — the downstream skill has diagnostic
      access that the orchestrator does not.
    - Your ONLY job is to route to the correct next step and pass the
      required arguments. The downstream skill does the actual work.

    FAILURE PREDICATES — when to follow on_failure:
    - test_check: {"passed": false}
    - merge_worktree: "error" key present in response
      (cleanup_succeeded=false means orphaned worktree/branch — the merge itself succeeded)
    - run_cmd: {"success": false}
    - run_skill: {"success": false}
    - classify_fix: "error" key present in response
    - push_to_remote: {"success": false} (also has "error" and "stderr" keys)

    OPTIONAL STEP SEMANTICS:
    - optional: true means the step is SKIPPED when its skip_when_false ingredient
      resolves to false. skip_when_false references are resolved server-side before
      the recipe is served — falsy steps are removed entirely; truthy steps appear
      without optional or skip_when_false fields (mandatory).
    - NEVER skip a step for any other reason (PR size, diff triviality, etc.).
    - A running optional step that returns success: false MUST follow on_failure.

    To CREATE a new recipe, use the /write-recipe skill.
    This tool is for loading and executing existing recipes.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. The correct way to run a recipe is to call this
    tool, then follow the YAML steps. Recipes live in .autoskillit/recipes/
    as .yaml files (NOT in .autoskillit/skills/ or any other directory).

    ``$<name>`` or ``/<name>`` denotes an in-session skill invocation. Do not pass
    a skill name to ``open_kitchen``, ``load_recipe``, ``migrate_recipe``, or
    ``recipe://``; those surfaces accept recipe identities only.
    A name defined as both a recipe and a skill is rejected until one artifact
    is renamed.

    This tool is strictly read-only. It discovers, parses, and validates recipe
    YAML. To run migrations, use migrate_recipe.

    Response format: always JSON with ``content`` (raw YAML string),
    ``diagram`` (pre-generated Markdown string or null), and
    ``suggestions`` (list of semantic findings, possibly empty) keys.
    On error: JSON with ``error`` key.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="load_recipe"):
            tool_ctx = _get_ctx_or_none()
            if tool_ctx is None or tool_ctx.recipes is None:
                return json.dumps({"error": "Server not initialized"})
            suppressed = tool_ctx.config.migration.suppressed
            _defaults = resolve_ingredient_defaults(tool_ctx.project_dir)
            _recipe_info_pre = _admit_recipe_name(tool_ctx, name)
            _raw_recipe_obj = tool_ctx.recipes.load(_recipe_info_pre.path)
            _session_overrides: dict[str, str] = {
                "kitchen_id": tool_ctx.kitchen_id,
                "diagnostics_log_dir": str(resolve_log_dir(tool_ctx.config.linux_tracing.log_dir)),
            }
            _config_layer = build_config_authoritative_layer(_defaults)
            _config_default = build_config_default_layer(_defaults)
            _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
                _raw_recipe_obj.steps if _raw_recipe_obj is not None else None,
                tool_ctx.backend.name if tool_ctx.backend else None,
                name,
                config_backend=tool_ctx.config.agent_backend,
            )
            _backend_capabilities_map = build_backend_capabilities_map(
                _effective_backend_map, tool_ctx.backend
            )
            result = serve_recipe(
                tool_ctx,
                name,
                caller_overrides=overrides,
                config_default=_config_default,
                session_overrides=_session_overrides,
                config_layer=_config_layer,
                resolved_defaults=_defaults,
                suppressed=suppressed,
                temp_dir=tool_ctx.temp_dir,
                temp_dir_relpath=temp_dir_display_str(tool_ctx.config.workspace.temp_dir),
                backend_name=tool_ctx.backend.name if tool_ctx.backend else None,
                effective_backend_map=_effective_backend_map,
                backend_capabilities_map=_backend_capabilities_map,
                backend_origin_map=_backend_origin_map,
            )
            _finalized_projection = (
                pop_finalized_recipe_projection(result) if result.get("valid", False) else None
            )
            recipe_info = _recipe_info_pre
            result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
            if not result.get("valid", False):
                result["validation_failed"] = True
            _authority_warnings = build_authority_clobber_warnings(
                overrides or {}, _config_layer, caller_tool="load_recipe"
            )
            if _authority_warnings:
                result["warnings"] = _authority_warnings
            if ingredients_only:
                result = strip_ingredients_only_keys(result)
            _required_keys: frozenset[str] = frozenset()
            if not ingredients_only and result.get("valid", False):
                _required_keys = frozenset({"content"})
            _validation_err = _validate_result(
                result, required_keys=_required_keys, tool_name="load_recipe"
            )
            if _validation_err is not None:
                logger.warning(
                    "load_recipe_fail_closed",
                    tool="load_recipe",
                    stage="validate_result",
                )
                return _validation_err
            if not ingredients_only and result.get("valid", False):
                if _finalized_projection is None:
                    raise RuntimeError("valid recipe is missing its finalized projection")
                _prepared_generation = prepare_recipe_delivery_generation(
                    result,
                    recipe_name=name,
                    tool_ctx=tool_ctx,
                    finalized_projection=_finalized_projection,
                )
                return cast(
                    str,
                    finalize_recipe_delivery(
                        result,
                        surface="load_recipe",
                        recipe_name=name,
                        tool_ctx=tool_ctx,
                        finalized_projection=_finalized_projection,
                        flow_generation=_prepared_generation.flow_generation,
                        canonical_artifact_payload=(
                            _prepared_generation.canonical_artifact_payload
                        ),
                        execution_snapshot=_prepared_generation.execution_snapshot,
                        normalized_compile_key=(_prepared_generation.normalized_compile_key),
                        delivery_request=delivery_request,
                    ),
                )
            return render_served_response(result)
    except Exception as exc:
        logger.error("load_recipe unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
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
                if page_plan_sha256 != page_plan.page_plan_sha256:
                    return _recipe_section_failure("invalid_recipe_page_plan_identity")
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
            rendered_payload = json.loads(rendered)
            if isinstance(active_initialization, InitializingRecipe):
                completed_parts, total_parts, calls_remaining = (
                    recipe_initialization_progress_counts(
                        active_initialization,
                        section=section,
                        page_plan_sha256=page_plan.page_plan_sha256,
                        part=part,
                    )
                )
                rendered_payload.update(
                    completed_parts=completed_parts,
                    total_parts=total_parts,
                    calls_remaining=calls_remaining,
                )
                rendered = json.dumps(
                    rendered_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if len(rendered.encode("utf-8")) > request_state.recipe_section_bound_bytes:
                    return _recipe_section_failure("recipe_section_bound_too_small")
            content_sha256 = rendered_payload.get("content_sha256")
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


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@track_response_size("complete_recipe_initialization")
@_cancellation_shield()
async def complete_recipe_initialization(initialization_id: str) -> str:
    """Complete the current server-owned recipe initialization.

    The opaque initialization ID is the only caller value. Generation identities,
    reconstruction coverage, the staged snapshot, and the completion receipt are
    resolved from current kitchen state. The READY transition occurs only after the
    universal response boundary preserves the exact completion response.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        tool_ctx = _get_ctx_or_none()
        if tool_ctx is None:
            return json.dumps(
                {"success": False, "error": "kitchen not open"},
                separators=(",", ":"),
            )
        return cast(str, build_completion_response(tool_ctx, initialization_id))
    except Exception:
        logger.error(
            "complete_recipe_initialization unhandled exception",
            exc_info=True,
        )
        return json.dumps(
            {"success": False, "error": "recipe_initialization_internal_error"},
            separators=(",", ":"),
        )


def _extract_step_body_from_persisted(persisted: dict[str, Any], step_name: str) -> str:
    """Extract a single step's YAML subtree from the persisted full payload.

    The persisted payload's ``content`` field is the full recipe YAML
    rendered as a string (from ``load_and_validate``). We re-parse it
    via ``load_yaml`` to access the structured steps dict, then return
    only the requested step's sub-mapping serialized back to YAML.
    Returns an empty string only when the step is not present. Artifact parse
    and section serialization failures raise ``_RecipeSectionError`` so the
    caller can distinguish them from an absent section.
    """
    content = persisted.get("content", "") or ""
    if not content or not step_name:
        return ""
    try:
        parsed = load_yaml(content)
    except Exception as exc:
        logger.warning(
            "get_recipe_section_step_yaml_parse_failed",
            step_name=step_name,
            exc_info=True,
        )
        raise _RecipeSectionError(
            "recipe_artifact_parse_failed", f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise _RecipeSectionError(
            "recipe_artifact_parse_failed", "recipe content is not a mapping"
        )
    steps = parsed.get("steps")
    if not isinstance(steps, dict):
        raise _RecipeSectionError("recipe_artifact_parse_failed", "recipe steps are not a mapping")
    step_obj = steps.get(step_name)
    if step_obj is None:
        return ""
    if not isinstance(step_obj, dict):
        raise _RecipeSectionError(
            "recipe_section_serialization_failed",
            "recipe step is not a mapping",
        )
    # Render just this step's subtree as compact YAML.
    try:
        return fast_dumps({step_name: step_obj})
    except Exception as exc:
        logger.warning(
            "get_recipe_section_step_yaml_serialize_failed",
            step_name=step_name,
            exc_info=True,
        )
        raise _RecipeSectionError(
            "recipe_section_serialization_failed", f"{type(exc).__name__}: {exc}"
        ) from exc


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("validate_recipe")
async def validate_recipe(script_path: str) -> str:
    """Validate a recipe YAML file against the recipe schema.

    Parses the file, checks all validation rules (name, steps, routing,
    retry fields, ingredient references), and returns structured results.
    Use after generating or modifying a recipe (via write-recipe)
    to confirm it is valid. The /write-recipe skill
    calls this tool automatically after generating a recipe.

    When validation fails ({"valid": false}), do NOT edit the YAML file
    directly to fix errors. Use the /write-recipe skill
    to apply corrections — it has the complete schema, validation rules,
    and formatting constraints needed for correct modifications.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. They are loaded via load_recipe and executed
    step-by-step by the agent. Recipes live in .autoskillit/recipes/
    as .yaml files.

    Args:
        script_path: Absolute path to the .yaml recipe file to validate.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="validate_recipe"):
            tool_ctx = _get_ctx_or_none()
            if tool_ctx is None or tool_ctx.recipes is None:
                return json.dumps({"valid": False, "errors": ["Server not initialized"]})
            try:
                _raw_validate_recipe = tool_ctx.recipes.load(Path(script_path))
                _validate_recipe_name = _raw_validate_recipe.name
                _validate_recipe_steps = _raw_validate_recipe.steps
            except Exception:
                logger.warning("validate_recipe_load_failed", path=script_path, exc_info=True)
                _validate_recipe_name = ""
                _validate_recipe_steps = None
            _validate_effective_backend_map, _validate_backend_origin_map = (
                _compute_effective_backend_map(
                    _validate_recipe_steps,
                    tool_ctx.backend.name if tool_ctx.backend else None,
                    _validate_recipe_name,
                    config_backend=tool_ctx.config.agent_backend,
                )
            )
            _validate_backend_capabilities_map = build_backend_capabilities_map(
                _validate_effective_backend_map, tool_ctx.backend
            )
            result = tool_ctx.recipes.validate_from_path(
                Path(script_path),
                temp_dir_relpath=temp_dir_display_str(tool_ctx.config.workspace.temp_dir),
                backend_name=tool_ctx.backend.name if tool_ctx.backend else None,
                effective_backend_map=_validate_effective_backend_map,
                backend_capabilities_map=_validate_backend_capabilities_map,
                backend_origin_map=_validate_backend_origin_map,
            )
            return json.dumps(result)
    except Exception as exc:
        logger.error("validate_recipe unhandled exception", exc_info=True)
        return json.dumps({"valid": False, "errors": [f"{type(exc).__name__}: {exc}"]})


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("migrate_recipe")
async def migrate_recipe(name: str, ctx: Context = CurrentContext()) -> str:
    """Apply pending migration notes to a recipe file.

    This tool is gated — the kitchen must be open before calling it.

    Checks whether the named recipe has pending migration notes relative to the
    installed autoskillit version. If migrations are applicable, runs the
    migration engine (which launches a headless Claude session), writes the
    updated recipe back to disk, and regenerates the contract card.

    This tool sends MCP progress notifications via ctx during long-running
    migration engine invocations.

    Returns JSON with one of:
    - ``{"status": "up_to_date", "name": name}`` — no migrations needed
    - ``{"status": "migrated", "name": name}`` — migration completed successfully
    - ``{"error": "...", "name": name}`` — migration failed (details in error)
    - ``{"error": "No recipe named '...' found"}`` — recipe not found

    Args:
        name: The recipe name (without .yaml extension) to migrate.

    ``$<name>`` or ``/<name>`` denotes an in-session skill invocation. Do not pass
    a skill name to ``open_kitchen``, ``load_recipe``, ``migrate_recipe``, or
    ``recipe://``; those surfaces accept recipe identities only.
    A name defined as both a recipe and a skill is rejected until one artifact
    is renamed.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="migrate_recipe", recipe_name=name):
            logger.info("migrate_recipe", recipe_name=name)
            await _notify(
                ctx,
                "info",
                f"migrate_recipe: {name}",
                "autoskillit.migrate_recipe",
                extra={"recipe_name": name},
            )

            from autoskillit.server import (  # circular-break
                _get_config,
                _get_ctx,
            )  # circular-break: server-internal circular dependency

            tool_ctx = _get_ctx()

            try:
                recipe_info = _admit_recipe_name(tool_ctx, name)
            except RecipeLoadError as exc:
                return json.dumps({"error": str(exc), "name": name})

            # Check suppression list before attempting migration
            if name in _get_config().migration.suppressed:
                return json.dumps({"status": "up_to_date", "name": name})

            if tool_ctx.migrations is None:
                return json.dumps({"error": "Migration service not configured", "name": name})
            result = await tool_ctx.migrations.migrate(recipe_info.path)
            return json.dumps(result)
    except Exception as exc:
        logger.error("migrate_recipe unhandled exception", exc_info=True)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "name": name})
