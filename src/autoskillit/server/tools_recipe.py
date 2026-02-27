"""MCP tool handlers: load_recipe, list_recipes, validate_recipe, migrate_recipe."""

from __future__ import annotations

import functools
import json
from pathlib import Path

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit import __version__
from autoskillit.core.logging import get_logger
from autoskillit.migration.engine import MigrationFile, default_migration_engine
from autoskillit.migration.loader import applicable_migrations
from autoskillit.migration.store import FailureStore, default_store_path
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS  # noqa: F401
from autoskillit.server import mcp
from autoskillit.server.helpers import _require_enabled

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
    from autoskillit.recipe.io import list_recipes as _list_recipes

    result = _list_recipes(Path.cwd())
    response: dict[str, object] = {
        "recipes": [
            {"name": r.name, "description": r.description, "summary": r.summary}
            for r in result.items
        ],
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return json.dumps(response)


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
    from autoskillit.core.io import YAMLError, load_yaml
    from autoskillit.recipe.contracts import (
        check_contract_staleness,
        load_recipe_card,
        validate_recipe_cards,
    )
    from autoskillit.recipe.io import _parse_recipe, find_recipe_by_name
    from autoskillit.recipe.validator import run_semantic_rules

    _match = find_recipe_by_name(name, Path.cwd())
    if _match is None:
        return json.dumps({"error": f"No recipe named '{name}' found"})
    content = _match.path.read_text()

    # Resolve migration suppression list once before the try block.
    # Ungated tool: gracefully handle missing context (e.g. tests without tool_ctx).
    from autoskillit.server import _ctx

    _migration_suppressed: list[str] = _ctx.config.migration.suppressed if _ctx is not None else []

    suggestions: list[dict[str, str]] = []
    try:
        data = load_yaml(content)
        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)

            findings = run_semantic_rules(recipe)
            semantic_suggestions = [f.to_dict() for f in findings]

            if name in _migration_suppressed:
                semantic_suggestions = [
                    s for s in semantic_suggestions if s.get("rule") != "outdated-recipe-version"
                ]
            suggestions.extend(semantic_suggestions)

            # Contract validation
            recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
            contract = load_recipe_card(name, recipes_dir)

            if contract:
                contract_findings = validate_recipe_cards(recipe, contract)
                suggestions.extend(contract_findings)

                # Staleness check
                stale = check_contract_staleness(contract)
                for item in stale:
                    suggestions.append(
                        {
                            "rule": "stale-contract",
                            "severity": "warning",
                            "step": item.skill,
                            "message": (
                                f"Contract is stale: {item.reason} for "
                                f"'{item.skill}' (stored={item.stored_value}, "
                                f"current={item.current_value}). Consider "
                                f"regenerating the contract."
                            ),
                        }
                    )
    except YAMLError as exc:
        logger.warning(
            "Recipe YAML parse error",
            name=name,
            exc_info=True,
        )
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"YAML parse error: {exc}",
            }
        )
    except ValueError as exc:
        logger.warning(
            "Recipe structure invalid",
            name=name,
            exc_info=True,
        )
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"Invalid recipe structure: {exc}",
            }
        )
    except (FileNotFoundError, OSError) as exc:
        logger.warning(
            "Recipe file not found or unreadable",
            name=name,
            exc_info=True,
        )
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"File error: {exc}",
            }
        )
    # Unexpected exceptions (AttributeError, RuntimeError, etc.) propagate uncaught

    return json.dumps({"content": content, "suggestions": suggestions})


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
    from autoskillit.core.io import YAMLError, load_yaml
    from autoskillit.core.types import Severity
    from autoskillit.recipe.contracts import load_recipe_card, validate_recipe_cards
    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.validator import (
        analyze_dataflow,
        run_semantic_rules,
    )
    from autoskillit.recipe.validator import (
        validate_recipe as _validate_recipe,
    )

    path = Path(script_path)
    if not path.is_file():
        return json.dumps({"error": f"File not found: {script_path}"})

    try:
        data = load_yaml(path)
    except YAMLError as exc:
        return json.dumps({"error": f"YAML parse error: {exc}"})

    if not isinstance(data, dict):
        return json.dumps({"error": "File must contain a YAML mapping"})

    recipe = _parse_recipe(data)
    errors = _validate_recipe(recipe)
    report = analyze_dataflow(recipe)
    semantic_findings = run_semantic_rules(recipe)

    quality = {
        "warnings": [
            {
                "code": w.code,
                "step": w.step_name,
                "field": w.field,
                "message": w.message,
            }
            for w in report.warnings
        ],
        "summary": report.summary,
    }
    semantic = [f.to_dict() for f in semantic_findings]

    # Contract validation
    contract_findings: list[dict] = []
    recipes_dir = path.parent
    recipe_name = path.stem
    contract = load_recipe_card(recipe_name, recipes_dir)
    if contract:
        contract_findings = validate_recipe_cards(recipe, contract)

    has_schema_errors = bool(errors)
    has_semantic_errors = any(f.severity == Severity.ERROR for f in semantic_findings)
    has_contract_errors = any(f.get("severity") == "error" for f in contract_findings)
    valid = not has_schema_errors and not has_semantic_errors and not has_contract_errors

    return json.dumps(
        {
            "valid": valid,
            "errors": errors,
            "quality": quality,
            "semantic": semantic,
            "contracts": contract_findings,
        }
    )


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
    structlog.contextvars.bind_contextvars(tool="migrate_recipe", name=name)
    logger.info("migrate_recipe", name=name)
    try:
        await ctx.info(
            f"migrate_recipe: {name}",
            logger_name="autoskillit.migrate_recipe",
            extra={"name": name},
        )
    except (RuntimeError, AttributeError):
        pass

    from autoskillit.core.io import load_yaml
    from autoskillit.execution.headless import run_headless_core
    from autoskillit.recipe.contracts import generate_recipe_card
    from autoskillit.recipe.io import _parse_recipe, find_recipe_by_name
    from autoskillit.server import _ctx, _get_ctx

    project_dir = Path.cwd()
    _match = find_recipe_by_name(name, project_dir)
    if _match is None:
        return json.dumps({"error": f"No recipe named '{name}' found"})

    recipes_dir = project_dir / ".autoskillit" / "recipes"
    recipe_path = recipes_dir / f"{name}.yaml"
    content = _match.path.read_text()
    data = load_yaml(content)
    recipe = _parse_recipe(data)

    _migration_suppressed: list[str] = _ctx.config.migration.suppressed if _ctx is not None else []
    migrations = applicable_migrations(recipe.version, __version__)
    if not migrations or name in _migration_suppressed:
        return json.dumps({"status": "up_to_date", "name": name})

    temp_dir = project_dir / ".autoskillit" / "temp"
    failure_store = FailureStore(default_store_path(project_dir))
    engine = default_migration_engine()
    mfile = MigrationFile(
        name=name,
        path=recipe_path,
        file_type="recipe",
        current_version=recipe.version,
    )
    _bound_headless = functools.partial(run_headless_core, ctx=_get_ctx())
    migration_result = await engine.migrate_file(
        mfile,
        run_headless=_bound_headless,
        temp_dir=temp_dir,
    )

    if migration_result.success:
        failure_store.clear(name)
        try:
            if recipe_path.exists():
                generate_recipe_card(recipe_path, recipes_dir)
        except Exception:
            logger.warning(
                "migrate_recipe contract card generation failed",
                name=name,
                exc_info=True,
            )
        return json.dumps({"status": "migrated", "name": name})

    failure_store.record(
        name=name,
        file_path=recipe_path,
        file_type="recipe",
        error=migration_result.error or "unknown",
        retries_attempted=migration_result.retries_attempted,
    )
    return json.dumps({"error": f"Migration failed: {migration_result.error}", "name": name})
