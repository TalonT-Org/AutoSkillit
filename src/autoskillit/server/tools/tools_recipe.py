"""MCP recipe tool handlers, including compact artifact retrieval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit import __version__
from autoskillit.config import (
    build_config_authoritative_layer,
    build_config_default_layer,
    resolve_ingredient_defaults,
)
from autoskillit.core import (
    FLEET_DISPATCH_TOOLS,  # noqa: F401 — re-exported for downstream visibility
    ProcessStaleError,
    atomic_write,
    dump_yaml_str,
    get_logger,
    load_yaml,
    resolve_effective_delivery_bound,
    temp_dir_display_str,
)
from autoskillit.execution import resolve_worst_case_delivery_bound
from autoskillit.pipeline import GATED_TOOLS, UNGATED_TOOLS  # noqa: F401
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import (
    _apply_triage_gate,
    resolve_log_dir,
    strip_ingredients_only_keys,
)
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._response_budget import _artifact_path
from autoskillit.server._state import _get_ctx_or_none
from autoskillit.server.tools._authority_feedback import build_authority_clobber_warnings
from autoskillit.server.tools._auto_overrides import (
    _compute_effective_backend_map,
    _promote_capability_keys,
    _provider_aware_capability_overrides,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._serve_helpers import (
    build_and_record_recipe_envelope,
    build_backend_capabilities_map,
    render_served_response,
    response_backstop_tool_meta,
    serve_recipe,
)
from autoskillit.server.tools._types import _validate_result

logger = get_logger(__name__)


def _resolve_envelope_delivery_bound(tool_ctx: Any) -> int:
    """Resolve the envelope construction-time bound in bytes.

    Mirrors the resolution in ``track_response_size.wrapper`` so the envelope
    is constructed against the same gate that enforcement applies. Backend
    capabilities are preferred; falls back to the smallest registered backend
    bound (worst case) when capabilities are unavailable.
    """
    backend = getattr(tool_ctx, "backend", None)
    caps = getattr(backend, "capabilities", None) if backend is not None else None
    token_limit: int | None = None
    if caps is not None:
        try:
            token_limit = resolve_effective_delivery_bound(caps)
        except Exception:  # noqa: BLE001
            logger.warning("resolve_effective_delivery_bound_failed", exc_info=True)
            token_limit = None
    # Coerce to int; MagicMock or non-numeric values fall through to the
    # conservative default so envelope construction never crashes on a
    # misconfigured backend (e.g., a test mock with a MagicMock capabilities).
    if not isinstance(token_limit, int) or token_limit <= 0:
        try:
            fallback = resolve_worst_case_delivery_bound()
        except Exception:  # noqa: BLE001
            logger.warning("resolve_worst_case_delivery_bound_failed", exc_info=True)
            fallback = 0
        if isinstance(fallback, int) and fallback > 0:
            token_limit = fallback
        else:
            token_limit = 10_000
    return token_limit * 4


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
@_cancellation_shield()
@track_response_size("load_recipe")
async def load_recipe(
    name: str, overrides: dict[str, str] | None = None, ingredients_only: bool = False
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
            _recipe_info_pre = tool_ctx.recipes.find(name, tool_ctx.project_dir)
            _raw_recipe_obj = (
                tool_ctx.recipes.load(_recipe_info_pre.path)
                if _recipe_info_pre is not None
                else None
            )
            _session_overrides: dict[str, str] = {
                "kitchen_id": tool_ctx.kitchen_id,
                "diagnostics_log_dir": str(resolve_log_dir(tool_ctx.config.linux_tracing.log_dir)),
            }
            _provider_overrides, _cap_detail = _provider_aware_capability_overrides(
                tool_ctx.backend,
                name,
                tool_ctx.config.providers,
                _raw_recipe_obj.steps if _raw_recipe_obj is not None else None,
                skill_resolver=tool_ctx.skill_resolver,
                config_backend=tool_ctx.config.agent_backend,
            )
            _session_overrides.update(_provider_overrides)
            _config_layer = build_config_authoritative_layer(_defaults)
            _config_default = build_config_default_layer(_defaults)
            _promote_capability_keys(_config_layer, _session_overrides)
            _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
                _raw_recipe_obj.steps if _raw_recipe_obj is not None else None,
                tool_ctx.backend.name if tool_ctx.backend else None,
                tool_ctx.config.providers,
                name,
                skill_resolver=tool_ctx.skill_resolver,
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
            recipe_info = _recipe_info_pre
            result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
            if not result.get("valid", False):
                result["validation_failed"] = True
            if not result.get("dispatch_feasible", True):
                result["dispatch_infeasible"] = True
                result["user_visible_message"] = (
                    f"Recipe is infeasible on current backend: "
                    f"steps {result.get('infeasible_steps', [])} route to terminal failure."
                )
                _infeasible_envelope: dict[str, Any] = {
                    "success": False,
                    "dispatch_infeasible": True,
                    "infeasible_steps": result.get("infeasible_steps", []),
                    "user_visible_message": result["user_visible_message"],
                }
                if _cap_detail is not None and _cap_detail.resolution_path == "none_pass":
                    _missing = list(_cap_detail.missing_provider_steps)
                    _infeasible_envelope["missing_provider_steps"] = _missing
                    _infeasible_envelope["escape_hatch"] = (
                        f"Add provider overrides with ANTHROPIC_BASE_URL for steps: "
                        f"{_missing}. Example config: "
                        f"providers.recipe_overrides.<recipe>.*: <profile>"
                    )
                    _infeasible_envelope["user_visible_message"] = (
                        f"Recipe is infeasible on current backend: steps "
                        f"{_missing} lack ANTHROPIC_BASE_URL provider overrides. "
                        f"{_infeasible_envelope['escape_hatch']}"
                    )
                return json.dumps(_infeasible_envelope)
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
            # Part B Step 2.4: persist the full recipe artifact and return a
            # compact envelope. The artifact is the authoritative source of
            # full recipe content; the envelope carries routing metadata,
            # the step-flow skeleton, and pull instructions. ``get_recipe_section``
            # reads ``ctx.recipe_artifact_state`` to retrieve any section on demand.
            envelope = build_and_record_recipe_envelope(
                tool_ctx=tool_ctx,
                tool_name="load_recipe",
                payload=result,
                result=result,
                kitchen_label="loaded",
                version=__version__,
                overrides=overrides,
                recipe_name=name,
                ingredients_only=ingredients_only,
            )
            return render_served_response(envelope)
    except Exception as exc:
        logger.error("load_recipe unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


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
            _cap_overrides, _ = _provider_aware_capability_overrides(
                tool_ctx.backend,
                _validate_recipe_name,
                tool_ctx.config.providers,
                _validate_recipe_steps,
                skill_resolver=tool_ctx.skill_resolver,
                config_backend=tool_ctx.config.agent_backend,
            )
            _validate_effective_backend_map, _validate_backend_origin_map = (
                _compute_effective_backend_map(
                    _validate_recipe_steps,
                    tool_ctx.backend.name if tool_ctx.backend else None,
                    tool_ctx.config.providers,
                    _validate_recipe_name,
                    skill_resolver=tool_ctx.skill_resolver,
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
                ingredient_overrides=_cap_overrides,
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

            # Check suppression list before attempting migration
            if name in _get_config().migration.suppressed:
                return json.dumps({"status": "up_to_date", "name": name})

            if tool_ctx.recipes is None:
                return json.dumps({"error": "Recipe repository not configured"})
            recipe = tool_ctx.recipes.find(name, tool_ctx.project_dir)
            if recipe is None:
                return json.dumps({"error": f"No recipe named '{name}' found"})

            if tool_ctx.migrations is None:
                return json.dumps({"error": "Migration service not configured", "name": name})
            result = await tool_ctx.migrations.migrate(recipe.path)
            return json.dumps(result)
    except Exception as exc:
        logger.error("migrate_recipe unhandled exception", exc_info=True)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "name": name})


# Part B Step 2.3: bounded pull tool for step / section content. Reads from
# the always-persisted artifact written by open_kitchen / load_recipe and
# verifies sha256 before serving. Falls back to load_and_validate recreation
# if the artifact file is missing from disk (e.g. after a session restart).
#
# Decorated WITHOUT ``meta=response_backstop_tool_meta(...)``: this tool is
# not in ``RESPONSE_BACKSTOP_EXEMPTION_REGISTRY``, so the universal response
# backstop applies directly. Per ``server/AGENTS.md`` all MCP tools MUST have
# ``readOnlyHint: True``.
@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("get_recipe_section")
async def get_recipe_section(
    section: str,
    step_name: str | None = None,
    part: int = 0,
) -> str:
    """Pull a bounded slice of recipe content from the persisted artifact.

    The companion tool to ``open_kitchen`` and ``load_recipe``: those tools
    return a compact step-skeleton envelope; this tool returns the full
    content for a single step (or other section) on demand. The orchestrator
    calls ``get_recipe_section(section="step", step_name=<name>)`` just
    before executing each step to keep context lean.

    Sections:
      - "step": full YAML for one step (requires ``step_name``)
      - "content": the full recipe content string (chunked if oversized)
      - "diagram": the pre-rendered diagram string
      - "suggestions": the full suggestions list

    Args:
        section: Which slice to retrieve — "step", "content", "diagram", or
            "suggestions".
        step_name: Required when ``section == "step"``; must be a valid
            post-prune step name from the most recent envelope's
            ``step_index``.
        part: 0-indexed chunk number for sections that exceed the delivery
            bound. Default 0 (first/only chunk). When the response is
            chunked, ``has_more`` and ``next_part`` fields describe how to
            retrieve the remainder.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="get_recipe_section", section=section):
            tool_ctx = _get_ctx_or_none()
            if tool_ctx is None:
                return json.dumps({"success": False, "error": "Server not initialized"})

            artifact_state = getattr(tool_ctx, "recipe_artifact_state", None)
            if not artifact_state:
                return json.dumps(
                    {
                        "success": False,
                        "error": "no_recipe_loaded",
                        "detail": (
                            "Call open_kitchen or load_recipe first so a "
                            "recipe artifact is persisted on this session."
                        ),
                    }
                )

            artifact_path = artifact_state.get("artifact_path")
            expected_sha256 = artifact_state.get("sha256")
            if not artifact_path or not expected_sha256:
                return json.dumps(
                    {
                        "success": False,
                        "error": "artifact_state_invalid",
                        "detail": "recipe_artifact_state is missing artifact_path or sha256",
                    }
                )

            payload = _read_or_recreate_artifact(tool_ctx, artifact_state)
            if isinstance(payload, dict) and payload.get("error"):
                return json.dumps(payload)

            assert isinstance(payload, dict)  # narrowed by _read_or_recreate_artifact
            return _build_section_response(
                payload=payload,
                section=section,
                step_name=step_name,
                part=part,
                artifact_path=artifact_path,
                expected_sha256=expected_sha256,
                tool_ctx=tool_ctx,
            )
    except Exception as exc:
        logger.error("get_recipe_section unhandled exception", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"{type(exc).__name__}: {exc}", "section": section}
        )


def _read_or_recreate_artifact(
    tool_ctx: Any,
    artifact_state: dict[str, Any],
) -> dict[str, Any]:
    """Read the persisted recipe artifact; recreate via load_and_validate if missing.

    Returns a dict payload on success (the recipe serve result) or an error
    dict (with an ``error`` key) on integrity / recreation failure. Never
    raises — exceptions inside ``load_and_validate`` are caught and reported
    as structured errors so the pull tool never silently returns empty.
    """
    artifact_path = artifact_state.get("artifact_path", "")
    expected_sha256 = artifact_state.get("sha256", "")
    try:
        path_obj = Path(artifact_path)
        serialized = path_obj.read_text(encoding="utf-8")
        actual_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256:
            return {
                "success": False,
                "error": "artifact_integrity_failed",
                "detail": (
                    f"sha256 mismatch: recorded={expected_sha256[:16]}... "
                    f"on_disk={actual_sha256[:16]}..."
                ),
            }
        try:
            return json.loads(serialized)
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": "artifact_corrupt",
                "detail": "Persisted artifact is not valid JSON.",
            }
    except FileNotFoundError:
        return _recreate_artifact(tool_ctx, artifact_state)
    except OSError as exc:
        return {
            "success": False,
            "error": "artifact_unavailable",
            "detail": f"Could not read artifact: {type(exc).__name__}: {exc}",
        }


def _recreate_artifact(
    tool_ctx: Any,
    artifact_state: dict[str, Any],
) -> dict[str, Any]:
    """Recreate a missing artifact by re-serving the recipe via serve_recipe().

    Routes through ``serve_recipe()`` (in ``_serve_helpers.py``) — the single
    legal call site for ``load_and_validate`` in ``server/tools/``. This
    preserves the SERVE_SURFACES contract enforced by
    ``tests/arch/test_serve_surface_registry.py``.

    Returns the recreated payload (on success) or an error dict (on failure).
    The recreated sha256 must match the recorded sha256 — divergent content
    is reported as an error rather than served.
    """

    recipe_name = artifact_state.get("recipe_name")
    if not recipe_name:
        return {
            "success": False,
            "error": "artifact_unavailable",
            "detail": "Cannot recreate artifact: recipe_name missing from artifact state.",
        }
    if tool_ctx.recipes is None:
        return {
            "success": False,
            "error": "artifact_unavailable",
            "detail": "Cannot recreate artifact: recipe repository not configured.",
        }

    ingredient_overrides = dict(artifact_state.get("ingredient_overrides") or {})
    backend_name = artifact_state.get("backend_name")
    try:
        recreated = serve_recipe(
            tool_ctx,
            recipe_name,
            caller_overrides=ingredient_overrides or None,
            config_default={},
            session_overrides={},
            config_layer={},
            backend_name=backend_name,
        )
    except ProcessStaleError as exc:
        return {
            "success": False,
            "error": "artifact_unavailable",
            "detail": f"Server package state is stale; restart required. ({exc})",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact_recreation_failed", exc_info=True)
        return {
            "success": False,
            "error": "artifact_unavailable",
            "detail": f"Recreation failed: {type(exc).__name__}: {exc}",
        }

    serialized = json.dumps(recreated, ensure_ascii=False, sort_keys=False)
    recreated_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    expected_sha256 = artifact_state.get("sha256", "")
    if recreated_sha256 != expected_sha256:
        return {
            "success": False,
            "error": "artifact_recreation_mismatch",
            "detail": (
                f"Recreated content diverges from original artifact "
                f"(sha256={recreated_sha256[:16]}... vs recorded={expected_sha256[:16]}...)"
            ),
        }

    artifact_dir = tool_ctx.temp_dir / "responses" / artifact_state.get("tool_name", "load_recipe")
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = _artifact_path(
            artifact_dir, artifact_state.get("tool_name", "load_recipe"), recreated_sha256
        )
        atomic_write(path, serialized)
    except OSError as exc:
        return {
            "success": False,
            "error": "artifact_unavailable",
            "detail": f"Recreated artifact could not be written: {type(exc).__name__}: {exc}",
        }
    return recreated


def _build_section_response(
    *,
    payload: dict[str, Any],
    section: str,
    step_name: str | None,
    part: int,
    artifact_path: str,
    expected_sha256: str,
    tool_ctx: Any,
) -> str:
    """Build the JSON response for a section pull, with optional chunking."""
    if section == "step":
        if not step_name:
            return json.dumps(
                {
                    "success": False,
                    "error": "step_name_required",
                    "detail": "section='step' requires step_name=<post_prune step name>",
                }
            )
        step_yaml = _extract_step_yaml(payload, step_name)
        if step_yaml is None:
            return json.dumps(
                {
                    "success": False,
                    "error": "step_not_found",
                    "detail": (
                        f"Step '{step_name}' is not in the loaded recipe's post-prune step set."
                    ),
                    "step_index": payload.get("post_prune_step_names", []),
                }
            )
        body: dict[str, Any] = {
            "success": True,
            "section": "step",
            "step_name": step_name,
            "content": step_yaml,
            "artifact_path": artifact_path,
            "sha256": expected_sha256,
        }
        return _chunk_response_if_oversized(body, tool_ctx, part=part)

    if section == "content":
        body = {
            "success": True,
            "section": "content",
            "content": payload.get("content", ""),
            "artifact_path": artifact_path,
            "sha256": expected_sha256,
        }
        return _chunk_response_if_oversized(body, tool_ctx, part=part)

    if section == "diagram":
        body = {
            "success": True,
            "section": "diagram",
            "content": payload.get("diagram"),
            "artifact_path": artifact_path,
            "sha256": expected_sha256,
        }
        return _chunk_response_if_oversized(body, tool_ctx, part=part)

    if section == "suggestions":
        body = {
            "success": True,
            "section": "suggestions",
            "content": payload.get("suggestions", []),
            "artifact_path": artifact_path,
            "sha256": expected_sha256,
        }
        return _chunk_response_if_oversized(body, tool_ctx, part=part)

    return json.dumps(
        {
            "success": False,
            "error": "unknown_section",
            "detail": (
                f"Unknown section {section!r}. Valid sections: 'step', 'content', "
                "'diagram', 'suggestions'."
            ),
        }
    )


def _extract_step_yaml(payload: dict[str, Any], step_name: str) -> str | None:
    """Extract the YAML block for a single step from the recipe content.

    Returns the full ``name: <step>\n  ...`` block (with ``steps:`` wrapper
    so downstream ``compact_recipe_display`` style transforms still apply),
    or ``None`` if the step is not in the post-prune step set.
    """
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return None
    try:
        parsed = load_yaml(content)
    except Exception:
        logger.warning("step_yaml_load_failed", step_name=step_name, exc_info=True)
        return None
    if not isinstance(parsed, dict):
        return None
    steps_obj = parsed.get("steps")
    if not isinstance(steps_obj, dict) or step_name not in steps_obj:
        return None
    step_value = steps_obj[step_name]
    try:
        return dump_yaml_str({"steps": {step_name: step_value}}, default_flow_style=False)
    except Exception:
        logger.warning("step_yaml_dump_failed", step_name=step_name, exc_info=True)
        return None


def _chunk_response_if_oversized(body: dict[str, Any], tool_ctx: Any, *, part: int = 0) -> str:
    """Split response by ``part`` if it exceeds the envelope delivery bound.

    The bound is the same one ``build_recipe_envelope`` uses (smallest
    registered backend delivery bound). Sections that fit return directly;
    oversized sections are chunked and carry ``has_more`` + ``next_part``.
    """
    bound = _resolve_envelope_delivery_bound(tool_ctx)
    serialized = json.dumps(body, ensure_ascii=False)
    body_bytes = len(serialized.encode("utf-8"))
    if body_bytes <= bound:
        return serialized
    chunk_size = max(1024, bound - 512)
    content_text = body.get("content")
    if not isinstance(content_text, (str, list)):
        return serialized
    total = len(content_text) if isinstance(content_text, str) else len(json.dumps(content_text))
    chunks = max(1, (total + chunk_size - 1) // chunk_size)
    if part < 0 or part >= chunks:
        return json.dumps(
            {
                "success": False,
                "error": "part_out_of_range",
                "detail": f"part={part} is out of range [0, {chunks}).",
                "total_parts": chunks,
            }
        )
    sliced: Any
    if isinstance(content_text, str):
        sliced = content_text[part * chunk_size : (part + 1) * chunk_size]
    else:
        text_repr = json.dumps(content_text)
        sliced_text = text_repr[part * chunk_size : (part + 1) * chunk_size]
        try:
            sliced = json.loads(sliced_text)
        except (TypeError, ValueError):
            sliced = []
    chunked = {
        "success": True,
        "section": body.get("section"),
        "step_name": body.get("step_name"),
        "content": sliced,
        "artifact_path": body.get("artifact_path"),
        "sha256": body.get("sha256"),
        "part": part,
        "total_parts": chunks,
        "has_more": part < chunks - 1,
        "next_part": part + 1 if part < chunks - 1 else None,
    }
    return json.dumps(chunked, ensure_ascii=False)
