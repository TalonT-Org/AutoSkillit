"""MCP tool handlers: load_recipe, list_recipes, validate_recipe, migrate_recipe."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.pipeline import GATED_TOOLS, UNGATED_TOOLS  # noqa: F401
from autoskillit.server import mcp
from autoskillit.server.helpers import _notify, _require_enabled

logger = get_logger(__name__)


@mcp.tool(tags={"automation"})
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

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).
    """
    from autoskillit.server import _ctx

    if _ctx is None or _ctx.recipes is None:
        return json.dumps([])
    result = _ctx.recipes.list_all(Path.cwd())
    return json.dumps(result)


@mcp.tool(tags={"automation"})
async def load_recipe(name: str) -> str:
    """Load a recipe by name and return its raw YAML content.

    The YAML follows the recipe schema (ingredients, steps with tool/action,
    on_success/on_failure routing, retry blocks). The agent should interpret
    the YAML and execute the steps using the appropriate MCP tools.

    After loading:
    1. Present the recipe to the user using the preview format below
    2. If the user requests changes, use the /autoskillit:write-recipe skill
       to apply modifications. That skill has the complete schema, validation rules,
       and formatting constraints needed for correct changes. Do NOT edit the YAML
       file directly — always delegate modifications to write-recipe.
    3. Prompt for input values using AskUserQuestion
    4. Execute the pipeline steps by calling MCP tools directly

    Preview format for step 1:

        ## {name}
        {description}

        **Flow:** {summary}

        ### Graph
        Render a route table showing the full execution flow. Use this exact
        column layout (align columns with spaces):

          Step               Tool                  ✓ success           ✗ failure
          ───────────────────────────────────────────────────────────────────────
          {step}             {tool/action/python}  → {on_success}      → {on_failure}

        Rules:
        - List steps in YAML declaration order.
        - For the Tool column: use the tool/action/python value. Append
          [model] if a model is set, e.g. "run_skill [sonnet]".
        - If on_success routes back to an earlier step, append ↑ to the name.
        - If on_failure routes back to an earlier step, append ↑ to the name.
        - If a step has retry: add an indented continuation line below it:
              ↺ ×{max_attempts} ({on} condition)  → {on_exhausted}
        - If a step uses on_result instead of on_success: leave the ✓ success
          cell empty and add indented continuation lines for each route:
              {route_key}  → {route_target}
          Append ↑ to any target that is an earlier step.
        - Terminal steps (action: stop) are excluded from the table and
          listed below the closing rule, one per line:
              {name}  "{message}"
        - Close the table with the same ─── rule used to open it.

        ### Ingredients
        For each ingredient show: name, description, required/optional, default value.
        Distinguish user-supplied ingredients (required=true or meaningful defaults)
        from agent-managed state (default="" or default=null with description
        indicating it is set by a prior step or the agent).

        ### Steps
        For each non-terminal step show:
        - Step name and tool/action/python discriminator
        - If optional: true, mark as "[Optional]" and show the note
        - If retry block exists: retries Nx on {condition}, then → {on_exhausted}
        - If note exists, show it (notes contain critical agent instructions)
        - If capture exists, show what values are extracted
        - If model: show the model value (e.g., "Model: sonnet")

        ### Kitchen Rules
        If present, list all kitchen_rules strings.
        If absent, note: "No kitchen rules defined"

    NEVER use native Claude Code tools from the orchestrator during pipeline
    execution. The following are prohibited: Read, Grep, Glob, Edit, Write,
    Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit.
    - Code investigation happens inside headless sessions launched by
      run_skill/run_skill_retry, which have full tool access.
    - Code modification is delegated through run_skill/run_skill_retry.
    - Shell commands use run_cmd, not the native Bash tool.
    - Research and multi-step work are delegated via run_skill.

    Allowed during pipeline execution:
    - AutoSkillit MCP tools (call directly, not via subagents)
    - AskUserQuestion (user interaction)
    - Steps with `capture:` fields extract values from tool results into a
      pipeline context dict. Use captured values in subsequent steps via
      ${{ context.var_name }} in `with:` arguments.
    - Thread outputs from each step into the next (e.g. worktree_path from
      implement into test_check).
    - Steps with a `model:` field: when calling `run_skill` or `run_skill_retry`,
      pass the step's `model` value as the `model` parameter to the tool.

    TOKEN USAGE TRACKING:
    - BEFORE executing the pipeline, call kitchen_status() and read
      token_usage_verbosity. This controls how you handle token reporting:
        "summary" → call get_token_summary(clear=True) ONCE after the
                     pipeline completes and render the table below.
        "none"    → do NOT call get_token_summary. Skip token reporting entirely.
    - Do NOT print or render a token usage table after individual steps.
      Only one call to get_token_summary is permitted per pipeline run,
      at the very end. Intermediate rendering is prohibited.
    - Pass step_name (the YAML step key, e.g. "implement") in the with: block
      when calling run_skill or run_skill_retry. The server accumulates token
      usage server-side, grouped by step name.
    - When verbosity is "summary", call get_token_summary(clear=True) at pipeline
      completion and render as:

      ## Token Usage Summary
      | Step | input | output | cache_create | cache_read |
      |------|-------|--------|--------------|------------|
      | investigate | 7 | 5939 | 8495 | 252179 |
      | implement | 2031 | 122306 | 280601 | 19,071,323 |
      | **Total** | ... | ... | ... | ... |

    - Non-skill steps (test_check, run_cmd, merge_worktree) have no token usage —
      they are not included in get_token_summary output. Do not add rows for them.

    ROUTING RULES — MANDATORY:
    - When a tool returns a failure result, you MUST follow the step's on_failure route.
    - When a step fails, route to on_failure — do not use Read, Grep, Glob, Edit,
      Write, Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit or any native
      tool to investigate. The on_failure step (e.g., assess-and-merge) has
      diagnostic access that the orchestrator does not.
    - Your ONLY job is to route to the correct next step and pass the
      required arguments. The downstream skill does the actual work.

    FAILURE PREDICATES — when to follow on_failure:
    - test_check: {"passed": false}
    - merge_worktree: "error" key present in response
      (cleanup_succeeded=false means orphaned worktree/branch — the merge itself succeeded)
    - run_cmd: {"success": false}
    - run_skill / run_skill_retry: {"success": false}
    - classify_fix: "error" key present in response

    To CREATE a new recipe, use the /autoskillit:write-recipe skill.
    This tool is for loading and executing existing recipes.

    IMPORTANT: Recipes are NOT slash commands. They cannot be invoked
    as /autoskillit:<name>. The correct way to run a recipe is to call this
    tool, then follow the YAML steps. Recipes live in .autoskillit/recipes/
    as .yaml files (NOT in .autoskillit/skills/ or any other directory).

    This tool is strictly read-only. It discovers, parses, and validates recipe
    YAML. To run migrations, use migrate_recipe.

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).

    Response format: always JSON with ``content`` (raw YAML string) and
    ``suggestions`` (list of semantic findings, possibly empty) keys.
    On error: JSON with ``error`` key.
    """
    from autoskillit.server import _ctx

    if _ctx is None or _ctx.recipes is None:
        return json.dumps({"error": "Server not initialized"})
    suppressed = _ctx.config.migration.suppressed
    result = _ctx.recipes.load_and_validate(name, Path.cwd(), suppressed=suppressed)
    return json.dumps(result)


@mcp.tool(tags={"automation"})
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

    This tool is always available (not gated by open_kitchen).
    This tool sends no MCP progress notifications by design (ungated tools are
    notification-free — see CLAUDE.md).

    Args:
        script_path: Absolute path to the .yaml recipe file to validate.
    """
    from autoskillit.server import _ctx

    if _ctx is None or _ctx.recipes is None:
        return json.dumps({"valid": False, "errors": ["Server not initialized"]})
    result = _ctx.recipes.validate_from_path(Path(script_path))
    return json.dumps(result)


@mcp.tool(tags={"automation"})
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
    """
    if (gate := _require_enabled()) is not None:
        return gate
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
