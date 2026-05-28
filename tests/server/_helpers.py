"""Shared test builder utilities for tests/server/."""

from __future__ import annotations

from typing import Any

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from tests.fleet._helpers import _make_recipe_info as _fleet_make_recipe_info

_HOOK_CONFIG_OVERLAY_RELPATH = (".autoskillit", "temp", ".hook_config_overlay.json")


def _simple_prompt_builder(**kwargs) -> str:
    """Minimal prompt builder for tests — avoids CLI imports."""
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


async def _no_sleep_quota_checker(config: Any, **kwargs) -> dict:
    """Quota checker stub: always returns no-sleep result."""
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config: Any, **kwargs) -> None:
    """Quota refresher stub: no-op."""


def _make_recipe_info(name: str = "test-recipe"):
    return _fleet_make_recipe_info(name, path_prefix="/fake/recipes/")


def _make_standard_recipe(name: str = "test-recipe", ingredient_keys: list[str] | None = None):
    """Return a minimal Recipe with kind=STANDARD for use as load_recipe mock return value."""
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

    ingredients = {k: RecipeIngredient(description=k) for k in (ingredient_keys or [])}
    return Recipe(name=name, description="test", ingredients=ingredients, kind=RecipeKind.STANDARD)


def _skill_ok(report_text: str = "## Bug Report\ndetails") -> SkillResult:
    return SkillResult(
        success=True,
        result=report_text,
        session_id="sid",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def _skill_fail() -> SkillResult:
    return SkillResult(
        success=False,
        result="",
        session_id="",
        subtype="error",
        is_error=True,
        exit_code=1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="something went wrong",
    )


_PATCHED_DEFAULTS = {
    "base_branch": "develop",
    "local_review_rounds": "7",
    "adversarial_review_level": "aggressive",
    "post_run_diagnostics": "true",
    "is_fleet_dispatch": "true",
    "dispatch_id": "test-dispatch-999",
}

_SERVER_ONLY_KEYS = frozenset({"kitchen_id", "diagnostics_log_dir"})

_MINIMAL_SCRIPT_YAML = """\
name: test-script
description: Test
summary: test
ingredients:
  task:
    description: What to do
    required: true
steps:
  do-thing:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Follow routing rules"
"""
