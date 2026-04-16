"""MCP tool handlers: load_recipe, list_recipes, validate_recipe, migrate_recipe."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.config import resolve_ingredient_defaults
from autoskillit.core import get_logger, temp_dir_display_str
from autoskillit.pipeline import GATED_TOOLS, UNGATED_TOOLS  # noqa: F401
from autoskillit.server import mcp
from autoskillit.server._state import _get_ctx_or_none
from autoskillit.server.helpers import (
    _apply_triage_gate,
    _notify,
    _require_enabled,
    track_response_size,
)

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen"}, annotations={"readOnlyHint": True})
@track_response_size("list_recipes")
async def list_recipes() -> str:
    """List available recipes from .autoskillit/recipes/.

    Returns a JSON array of recipes with name, description, and summary.
    Recipes are YAML workflow definitions that agents follow as orchestration
    instructions. Use load_recipe to load a specific recipe.
    To create a new recipe, use the /autoskillit:write-recipe skill.
    To generate recipes as part of project onboarding, use /autoskillit:setup-project.

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
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="list_recipes")
        tool_ctx = _get_ctx_or_none()
        if tool_ctx is None or tool_ctx.recipes is None:
            return json.dumps([])
        result = tool_ctx.recipes.list_all(Path.cwd())
        return json.dumps(result)
    except Exception:
        logger.error("list_recipes unhandled exception", exc_info=True)
        return json.dumps([])


@mcp.tool(
    tags={"autoskillit", "kitchen"},
    annotations={"readOnlyHint": True},
    meta={"anthropic/maxResultSizeChars": 100_000},
)
@track_response_size("load_recipe")
async def load_recipe(name: str, overrides: dict[str, str] | None = None) -> str:
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
    3. If the user requests changes, use the /autoskillit:write-recipe skill
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
      is false. When the ingredient is true (or absent), the step is MANDATORY.
    - NEVER skip a step for any other reason (PR size, diff triviality, etc.).
    - A running optional step that returns success: false MUST follow on_failure.

    To CREATE a new recipe, use the /autoskillit:write-recipe skill.
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
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="load_recipe")
        tool_ctx = _get_ctx_or_none()
        if tool_ctx is None or tool_ctx.recipes is None:
            return json.dumps({"error": "Server not initialized"})
        suppressed = tool_ctx.config.migration.suppressed
        _defaults = resolve_ingredient_defaults(Path.cwd())
        result = tool_ctx.recipes.load_and_validate(
            name,
            Path.cwd(),
            suppressed=suppressed,
            resolved_defaults=_defaults,
            ingredient_overrides=overrides,
            temp_dir=tool_ctx.temp_dir,
            temp_dir_relpath=temp_dir_display_str(tool_ctx.config.workspace.temp_dir),
        )
        recipe_info = tool_ctx.recipes.find(name, Path.cwd())
        return json.dumps(await _apply_triage_gate(result, name, recipe_info=recipe_info))
    except Exception as exc:
        logger.error("load_recipe unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen"}, annotations={"readOnlyHint": True})
@track_response_size("validate_recipe")
async def validate_recipe(script_path: str) -> str:
    """Validate a recipe YAML file against the recipe schema.

    Parses the file, checks all validation rules (name, steps, routing,
    retry fields, ingredient references), and returns structured results.
    Use after generating or modifying a recipe (via write-recipe)
    to confirm it is valid. The /autoskillit:write-recipe skill
    calls this tool automatically after generating a recipe.

    When validation fails ({"valid": false}), do NOT edit the YAML file
    directly to fix errors. Use the /autoskillit:write-recipe skill
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
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="validate_recipe")
        tool_ctx = _get_ctx_or_none()
        if tool_ctx is None or tool_ctx.recipes is None:
            return json.dumps({"valid": False, "errors": ["Server not initialized"]})
        result = tool_ctx.recipes.validate_from_path(
            Path(script_path),
            temp_dir_relpath=temp_dir_display_str(tool_ctx.config.workspace.temp_dir),
        )
        return json.dumps(result)
    except Exception as exc:
        logger.error("validate_recipe unhandled exception", exc_info=True)
        return json.dumps({"valid": False, "errors": [f"{type(exc).__name__}: {exc}"]})


@mcp.tool(tags={"autoskillit", "kitchen"}, annotations={"readOnlyHint": True})
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
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(tool="migrate_recipe", recipe_name=name)
        logger.info("migrate_recipe", recipe_name=name)
        await _notify(
            ctx,
            "info",
            f"migrate_recipe: {name}",
            "autoskillit.migrate_recipe",
            extra={"recipe_name": name},
        )

        from autoskillit.server import _get_config, _get_ctx

        tool_ctx = _get_ctx()

        # Check suppression list before attempting migration
        if name in _get_config().migration.suppressed:
            return json.dumps({"status": "up_to_date", "name": name})

        if tool_ctx.recipes is None:
            return json.dumps({"error": "Recipe repository not configured"})
        recipe = tool_ctx.recipes.find(name, Path.cwd())
        if recipe is None:
            return json.dumps({"error": f"No recipe named '{name}' found"})

        if tool_ctx.migrations is None:
            return json.dumps({"error": "Migration service not configured", "name": name})
        result = await tool_ctx.migrations.migrate(recipe.path)
        return json.dumps(result)
    except Exception as exc:
        logger.error("migrate_recipe unhandled exception", exc_info=True)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}", "name": name})
