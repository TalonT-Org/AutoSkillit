"""Orchestrator system prompt builder for the cook command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, get_logger, pkg_root
from autoskillit.franchise import _build_food_truck_prompt, _build_l2_sous_chef_block
from autoskillit.hooks import QUOTA_GUARD_DENY_TRIGGER, QUOTA_POST_WARNING_TRIGGER

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.recipe.loader import RecipeInfo


# Sentinel returned by _resolve_recipe_input when the user selects option 0.
_OPEN_KITCHEN_CHOICE: str = "__open_kitchen__"

__all__ = [
    "_build_food_truck_prompt",
    "_build_l2_sous_chef_block",
]


def _resolve_recipe_input(raw: str, available: list[RecipeInfo]) -> RecipeInfo | str | None:
    """Resolve picker raw text to a selection.

    Returns:
        _OPEN_KITCHEN_CHOICE  if raw is "0" (open kitchen, always valid)
        RecipeInfo            if raw is a valid 1-based index or an exact name match
        None                  for empty input, out-of-range numbers, or unknown names
    """
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if n == 0:
            return _OPEN_KITCHEN_CHOICE
        if 1 <= n <= len(available):
            return available[n - 1]
        return None
    return next((r for r in available if r.name == raw), None)


def _get_ingredients_table(
    recipe_name: str, recipe_info: RecipeInfo | None, cwd: Path
) -> str | None:
    """Pre-render the ingredients table for system prompt injection.

    Uses load_and_validate (not load_recipe) so sub-recipe composition is included.
    Returns None on any error so the orchestrator prompt is built without the
    ingredients table rather than crashing.
    """
    from autoskillit.config import resolve_ingredient_defaults
    from autoskillit.recipe import load_and_validate

    try:
        return load_and_validate(
            recipe_name,
            project_dir=cwd,
            recipe_info=recipe_info,
            resolved_defaults=resolve_ingredient_defaults(cwd),
        ).get("ingredients_table")
    except Exception:
        logger.warning(
            "Failed to pre-render ingredients table for %r — proceeding without it",
            recipe_name,
        )
        return None


_COOK_GREETINGS: list[str] = [
    (
        "Welcome to Good Burger, home of the Good Burger, "
        "can I take your order? Today's special: {recipe_name}."
    ),
    "Order up! Today's special: {recipe_name}. What ingredients are we working with?",
    "Table for one! Today's special: {recipe_name}. Ready when you are.",
    "Fresh off the menu — today's special: {recipe_name}. What can I get started for you?",
]

_OPEN_KITCHEN_GREETINGS: list[str] = [
    "Welcome to Good Burger, home of the Good Burger, can I take your order?",
    "Kitchen's open! What are we cooking today?",
    "Order up! The kitchen is ready. What can I get you?",
]


def _build_orchestrator_prompt(
    recipe_name: str,
    mcp_prefix: str,
    ingredients_table: str | None = None,
) -> str:
    """Build the --append-system-prompt content for a cook session.

    The prompt contains behavioral instructions (routing rules, failure
    predicates, orchestrator discipline) and a greeting pool. Recipe content
    is discovered by the session via ``load_recipe``.
    """
    # Inject sous-chef global orchestration rules (graceful degradation if absent)
    sous_chef_content = ""
    _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if _sous_chef_path.exists():
        sous_chef_content = "\n\n" + _sous_chef_path.read_text()

    _ing_section = ""
    if ingredients_table:
        _ing_section = (
            "RECIPE INGREDIENTS — USE THESE EXACT NAMES:\n"
            f"{ingredients_table}\n\n"
            "The ingredient names above are authoritative. Use them verbatim when:\n"
            "- Collecting values from the user\n"
            "- Evaluating skip_when_false conditions\n"
            "- Passing ingredients to pipeline steps via `with:` arguments\n\n"
        )

    return f"""\
You are a pipeline orchestrator. Execute the recipe '{recipe_name}' step-by-step.

{_ing_section}FIRST ACTION — before prompting for any inputs:
0. Call Bash(command="sleep 2") — this ensures MCP plugin tools are fully registered
   before proceeding. Bash is a built-in tool, always available. DO NOT SKIP THIS STEP.
1. Call ToolSearch(query='select:{mcp_prefix}open_kitchen') to ensure its schema is loaded.
   ToolSearch is a no-op if the schema is already loaded, but required if the tool appears
   in Claude Code's deferred-tool list. Always call it — no conditional check needed.
2. Call {mcp_prefix}open_kitchen(name='{recipe_name}') to activate pipeline tools and open
   the kitchen gate. open_kitchen is REQUIRED to enable all gated AutoSkillit tools —
   the ingredients table above (when present) is provided for reference only.
   DO NOT call AskUserQuestion or any other tool before open_kitchen.
3. The response contains a pre-formatted ingredients table
   between --- INGREDIENTS TABLE --- and --- END TABLE --- markers.
   Display it verbatim in your response — do not reformat or re-render it.
   Then ask for the required fields (marked with *). If the recipe has both
   a task and an issue_url ingredient, mention that a GitHub issue URL can
   be provided as the task. Keep it to one or two short sentences.
4. Collect ingredient values conversationally from the user's response.
5. Execute the pipeline steps.

During pipeline execution, only use AutoSkillit MCP tools:
- Read, Grep, Glob (code investigation) — not used here because investigation
  happens inside headless sessions launched by run_skill, which has full tool access.
- Edit, Write (code modification) — not used here because all code changes
  are delegated through run_skill.
- Bash (shell commands) — not used here; use run_cmd if shell access is needed.
- Agent subagents, WebFetch, WebSearch — not used here; delegate via
  run_skill for any research or multi-step work.

Allowed during pipeline execution:
- AutoSkillit MCP tools (call directly, not via subagents)
- AskUserQuestion (user interaction)
- Steps with `capture:` fields extract values from tool results into a
  pipeline context dict. Use captured values in subsequent steps via
  ${{{{ context.var_name }}}} in `with:` arguments.
- Thread outputs from each step into the next (e.g. worktree_path from
  implement into test_check).

ROUTING RULES — MANDATORY:
- When a tool returns a failure result, you MUST follow the step's on_failure route.
- When a step fails, route to on_failure — the downstream skill has diagnostic
  access that the orchestrator does not.
- Your ONLY job is to route to the correct next step and pass the
  required arguments. The downstream skill does the actual work.

FAILURE PREDICATES — when to follow on_failure:
- test_check: "passed: False" in output
- merge_worktree: "error:" line present in output
- run_cmd: "success: False" in output
- run_skill: "success: False" in output
- classify_fix: "error:" line present in output

FAILURE PREDICATE — open_kitchen:
  If the open_kitchen response contains `"success": false` OR does not
  contain the substring `--- INGREDIENTS TABLE ---`:
    1. Extract and print the value of "user_visible_message" from the
       JSON response verbatim (fall back to the raw response text if
       parsing fails).
    2. DO NOT call AskUserQuestion.
    3. End the session with a final text response.

CONTEXT LIMIT ROUTING — run_skill only (check BEFORE on_failure):
- When run_skill returns "success: False" AND "needs_retry: true" AND "retry_reason: resume":
  - Check "subtype" to discriminate the termination cause:
    - If subtype=stale: a transient hung process was killed by the watchdog. Retry
      the step (decrement the retries counter). Do NOT follow on_context_limit.
      If retries are exhausted, follow on_exhausted.
    - If subtype≠stale (e.g. context_exhaustion, error_max_turns): follow on_context_limit
      if defined, fall through to on_failure otherwise. This is the default resume path.
  - NEVER route retry_reason=resume with subtype=stale to on_context_limit.
- When run_skill returns "needs_retry: true" AND "retry_reason: drain_race":
  - The infrastructure confirmed session completion (Channel A or B) but stdout was not
    fully flushed before the process was killed. Partial progress was confirmed by the
    channel signal. Route identically to "resume": follow on_context_limit if defined,
    fall through to on_failure otherwise.
  - NEVER route retry_reason=drain_race to on_failure when on_context_limit exists.
- When run_skill returns "needs_retry: true" AND "retry_reason: empty_output":
  - The session exited cleanly but produced no output (transient API issue or infrastructure
    failure). No partial progress exists on disk. Do NOT route to on_context_limit.
  - Fall through to on_failure regardless of whether on_context_limit is defined.
- When run_skill returns "needs_retry: true" AND "retry_reason: path_contamination":
  - The session wrote files outside its working directory. This is a CWD boundary violation,
    not a context limit. No partial worktree progress should be resumed.
  - Fall through to on_failure regardless of whether on_context_limit is defined.
- When run_skill returns "needs_retry: true" AND "retry_reason: early_stop" or "zero_writes":
  - These are not context limit conditions. Fall through to on_failure.

QUOTA DENIAL ROUTING — run_skill only (check BEFORE on_failure):
- When a PreToolUse hook DENIES run_skill with "{QUOTA_GUARD_DENY_TRIGGER}":
  - This is a TEMPORARY block. The API quota resets on a rolling window.
  - The deny message contains a run_cmd sleep command. Execute it immediately.
  - After the sleep completes, retry the EXACT same run_skill call (same arguments).
  - NEVER treat a quota denial as a permanent failure or pipeline-stopping error.
  - NEVER report quota denials to the user — handle them silently and automatically.
  - Before executing the sleep, state aloud: "Quota denied. Sleeping N seconds, then retrying."
- When run_skill output contains "{QUOTA_POST_WARNING_TRIGGER}":
  - A post-execution quota check detected high utilization.
  - The warning contains a run_cmd sleep command. Execute it BEFORE the next run_skill call.
  - After sleeping, proceed normally with the next pipeline step.

TWO FAILURE TIERS FOR PREDICATE-FORMAT STEPS:
- Tool-level failure (run_skill returns "success: False"): Follow on_failure. This fires
  BEFORE any result object exists. on_result conditions are NOT evaluated.
- Skill-level error ("error:" line present in result): Follow the matching on_result
  condition. This fires only when run_skill completes and returns a result with an error line.
- When a step has no on_failure declared and the tool returns "success: False", this is a
  recipe authoring error. Stop the pipeline and report the missing route.

OPTIONAL STEP SEMANTICS:
- optional: true means the step is SKIPPED (treated as bypassed) when its
  skip_when_false ingredient is false. It does NOT mean failures are tolerated.
- When skip_when_false evaluates to true (or is absent), the step is MANDATORY
  and MUST execute. The ONLY reason to skip an optional step is skip_when_false
  being false — no other reason is valid.
- A running optional step that returns success: false MUST follow on_failure.
  Never route a running optional step's failure to done.

STEP EXECUTION IS NOT DISCRETIONARY:
- You MUST execute every step the pipeline routes you to.
- NEVER skip a step because the PR is small, the diff is trivial, the change
  looks simple, or you judge the step unnecessary.
- The ONLY mechanism for skipping a step is skip_when_false evaluating to false.
  When skip_when_false evaluates to true (or is absent), the step is MANDATORY.
- Consequence: skipping PR review steps results in unreviewed code, missing diff
  annotations, and no architectural lens analysis — code reaches main without
  quality gates.

ACTION: CONFIRM STEP SEMANTICS:
- When you reach a step with action: "confirm", call AskUserQuestion with the
  step's message. Do NOT call any MCP tools for this step type — user interaction
  via AskUserQuestion IS the step.
- If the user confirms (answers yes, ok, proceed, delete, or similar affirmative),
  route to the step's on_success target.
- If the user declines (answers no, skip, keep, cancel, or similar negative),
  route to the step's on_failure target.

SKILL_COMMAND FORMATTING — MANDATORY:
- The `skill_command` value in each step's `with:` block is a LITERAL template.
  Substitute ${{{{ context.var_name }}}} and ${{{{ inputs.var_name }}}} placeholders with
  their resolved values and pass the resulting string VERBATIM to run_skill.
- Do NOT add markdown headers, labels, notes, or any prose to skill_command.
  Do NOT restructure it as a labeled document or section list.
- skill_command arguments are POSITIONAL SPACE-SEPARATED TOKENS. A path argument
  is always a single path token — never a labeled section.
- If a step note says to pass an extra argument, append it as one space-separated
  token: `/autoskillit:skill /path/arg1 arg2`, NOT `/autoskillit:skill\n## Path\n/path`.
{sous_chef_content}
"""


def _build_open_kitchen_prompt(mcp_prefix: str) -> str:
    """Build the --append-system-prompt content for an open-kitchen cook session (no recipe)."""
    sous_chef_content = ""
    _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if _sous_chef_path.exists():
        sous_chef_content = "\n\n" + _sous_chef_path.read_text()

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
    text = (
        f'Call Bash(command="sleep 2") first — this ensures MCP plugin tools are fully '
        f"registered before proceeding. Bash is a built-in tool, always available.\n"
        f"Then call ToolSearch(query='select:{mcp_prefix}open_kitchen') to load the schema.\n"
        f"Then call {mcp_prefix}open_kitchen to open the AutoSkillit kitchen.\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill, which launches "
        "headless sessions with full tool access. Do NOT use native tools to "
        "investigate failures — route to on_failure and let the downstream skill "
        "handle diagnosis.\n\n"
        "OPTIONAL STEP SEMANTICS:\n"
        "- optional: true means the step is SKIPPED when its skip_when_false ingredient\n"
        "  is false. When skip_when_false evaluates to true (or is absent), the step is\n"
        "  MANDATORY. The ONLY reason to skip an optional step is skip_when_false being false.\n"
        "- A running optional step that returns success: false MUST follow on_failure.\n\n"
        "STEP EXECUTION IS NOT DISCRETIONARY:\n"
        "- You MUST execute every step the pipeline routes you to.\n"
        "- NEVER skip a step because the PR is small, the diff is trivial, the change\n"
        "  looks simple, or you judge the step unnecessary.\n"
        "- The ONLY mechanism for skipping a step is skip_when_false evaluating to false.\n"
        "- Consequence: skipping PR review steps results in unreviewed code, missing diff\n"
        "  annotations, and no architectural lens analysis — code reaches main without\n"
        "  quality gates." + sous_chef_content
    )

    scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
    recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        text += (
            "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated to the new recipe format.\n"
            "`.autoskillit/scripts/` still exists. Run `autoskillit upgrade` in this directory\n"
            "to migrate automatically, or ask me to do it for you."
        )

    return text


def show_cook_preview(
    recipe_name: str, parsed_recipe: object, recipes_dir: Path, project_dir: Path
) -> None:
    """Display the terminal preview: flow diagram + ingredients table.

    Owns the entire pre-launch display so ``cook()`` makes one call.
    Gateway imports only (no cross-package submodule imports).
    """
    from autoskillit.cli._ansi import diagram_to_terminal, ingredients_to_terminal
    from autoskillit.config import resolve_ingredient_defaults
    from autoskillit.recipe import build_ingredient_rows, load_recipe_diagram

    diagram = load_recipe_diagram(recipe_name, recipes_dir)
    if diagram:
        print(diagram_to_terminal(diagram))
        print()  # blank line between diagram and table

    resolved = resolve_ingredient_defaults(project_dir)
    rows = build_ingredient_rows(parsed_recipe, resolved_defaults=resolved)
    if rows:
        print(ingredients_to_terminal(rows))
