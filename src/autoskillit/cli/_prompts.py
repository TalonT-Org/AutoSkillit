"""Orchestrator system prompt builder for the cook command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, pkg_root
from autoskillit.execution import build_subrecipe_prompt as build_subrecipe_prompt

if TYPE_CHECKING:
    from autoskillit.recipe.loader import RecipeInfo


# Sentinel returned by _resolve_recipe_input when the user selects option 0.
_OPEN_KITCHEN_CHOICE: str = "__open_kitchen__"


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


def _build_orchestrator_prompt(script_yaml: str) -> str:
    """Build the --append-system-prompt content for a cook session."""
    # Inject sous-chef global orchestration rules (graceful degradation if absent)
    sous_chef_content = ""
    _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if _sous_chef_path.exists():
        sous_chef_content = "\n\n" + _sous_chef_path.read_text()

    return f"""\
You are a pipeline orchestrator. Execute the recipe below step-by-step.

1. Present the recipe to the user using the preview format below
2. Prompt for input values using AskUserQuestion
3. Execute the pipeline steps by calling MCP tools directly

Preview format:

    ## {{name}}
    {{description}}

    **Flow:** {{summary}}

    ### Ingredients
    For each ingredient show: name, description, required/optional, default value.
    Distinguish user-supplied ingredients (required=true or meaningful defaults)
    from agent-managed state (default="" or default=null with description
    indicating it is set by a prior step or the agent).

    ### Steps
    For each step show:
    - Step name and tool/action/python discriminator
    - Routing: on_success → X, on_failure → Y
    - If on_result: show field name and each route
    - If optional: true, mark as "[Optional]" and show the note explaining
      the skip condition
    - If retry block exists: retries Nx on {{condition}}, then → {{on_exhausted}}
    - If note exists, show it (notes contain critical agent instructions)
    - If capture exists, show what values are extracted

    ### Kitchen Rules
    If present, list all kitchen_rules strings.
    If absent, note: "No kitchen rules defined"

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
- A running optional step that returns success: false MUST follow on_failure.
  Never route a running optional step's failure to done.

ACTION: CONFIRM STEP SEMANTICS:
- When you reach a step with action: "confirm", call AskUserQuestion with the
  step's message. Do NOT call any MCP tools for this step type — user interaction
  via AskUserQuestion IS the step.
- If the user confirms (answers yes, ok, proceed, delete, or similar affirmative),
  route to the step's on_success target.
- If the user declines (answers no, skip, keep, cancel, or similar negative),
  route to the step's on_failure target.
{sous_chef_content}
--- RECIPE ---
{script_yaml}
--- END RECIPE ---
"""


def _build_open_kitchen_prompt() -> str:
    """Build the --append-system-prompt content for an open-kitchen cook session (no recipe)."""
    sous_chef_content = ""
    _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if _sous_chef_path.exists():
        sous_chef_content = "\n\n" + _sous_chef_path.read_text()

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
    text = (
        "Call the open_kitchen tool now to open the AutoSkillit kitchen and gain access to "
        "all automation tools. Then call the kitchen_status tool to display version "
        "and health information to the user.\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill, which launches "
        "headless sessions with full tool access. Do NOT use native tools to "
        "investigate failures — route to on_failure and let the downstream skill handle diagnosis."
        + sous_chef_content
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
