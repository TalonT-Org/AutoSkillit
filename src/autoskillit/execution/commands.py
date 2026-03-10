"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field

from autoskillit.core import ClaudeFlags, pkg_root


@dataclass(frozen=True)
class ClaudeInteractiveCmd:
    cmd: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class ClaudeHeadlessCmd:
    cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)  # always {}


def build_interactive_cmd(*, model: str | None = None) -> ClaudeInteractiveCmd:
    """Build a Claude interactive session command with kitchen pre-opened."""
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeInteractiveCmd(cmd=cmd, env={"AUTOSKILLIT_KITCHEN_OPEN": "1"})


def build_headless_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless session command for skill execution."""
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={})


def build_subrecipe_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless command for sub-recipe orchestration.

    Sets AUTOSKILLIT_KITCHEN_OPEN=1 so make_context() (factory.py) pre-enables
    the gate and server/__init__.py pre-reveals all kitchen tools at import time.
    AUTOSKILLIT_HEADLESS is intentionally NOT set — sub-recipe sessions must not
    trigger the open_kitchen_guard hook since they never call open_kitchen.
    """
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={"AUTOSKILLIT_KITCHEN_OPEN": "1"})


def build_subrecipe_prompt(recipe_yaml: str, ingredients_json: str) -> str:
    """Build the headless prompt for a sub-recipe orchestrator session.

    Like _build_orchestrator_prompt() but pre-supplies all ingredient values
    so the session begins execution immediately without AskUserQuestion collection.
    """
    try:
        ingredients = _json.loads(ingredients_json) if ingredients_json else {}
    except _json.JSONDecodeError:
        ingredients = {}

    sous_chef_content = ""
    _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if _sous_chef_path.exists():
        sous_chef_content = "\n\n" + _sous_chef_path.read_text()

    ingredients_block = "\n".join(f"  {k}: {v}" for k, v in ingredients.items()) or "  (none)"

    return f"""\
You are a pipeline sub-recipe orchestrator. Execute the recipe below immediately.

ALL ingredient values are pre-supplied. DO NOT use AskUserQuestion to collect them.
Begin execution immediately using the values provided.

Pre-supplied ingredients:
{ingredients_block}

During pipeline execution, only use AutoSkillit MCP tools. NEVER use native Claude Code
tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit).

ROUTING RULES — MANDATORY:
- When a tool returns a failure result, MUST follow the step's on_failure route.
- Your ONLY job is to route to the correct next step and pass required arguments.

FAILURE PREDICATES — when to follow on_failure:
- test_check: "passed: False" in output
- merge_worktree: "error:" line present in output
- run_cmd / run_skill: "success: False" in output
- classify_fix: "error:" line present in output

TWO FAILURE TIERS FOR PREDICATE-FORMAT STEPS:
- Tool-level failure ("success: False"): Follow on_failure. on_result NOT evaluated.
- Skill-level error ("error:" in result): Follow matching on_result condition.

OPTIONAL STEP SEMANTICS:
- optional: true means skipped when skip_when_false ingredient is false.
- A running optional step that fails MUST follow on_failure.

ACTION: CONFIRM STEP SEMANTICS:
- action: confirm → AskUserQuestion. Affirmative → on_success. Negative → on_failure.
{sous_chef_content}
--- RECIPE ---
{recipe_yaml}
--- END RECIPE ---
"""
