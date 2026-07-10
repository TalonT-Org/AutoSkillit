"""Shared test builder utilities for tests/server/."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import SkillResult
from autoskillit.core.types import (
    InputSpec,
    InputType,
    RetryReason,
)
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


def _patch_dispatch_quota_no_sleep(monkeypatch: Any) -> None:
    """Patch dispatch_food_truck's quota dependencies for non-quota tests."""
    monkeypatch.setattr(
        "autoskillit.server._misc.check_and_sleep_if_needed",
        _no_sleep_quota_checker,
    )
    monkeypatch.setattr(
        "autoskillit.server._misc._refresh_quota_cache",
        _noop_quota_refresher,
    )


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
    "is_fleet_dispatch": "true",
    "dispatch_id": "test-dispatch-999",
    "post_run_diagnostics": "true",
}

_SERVER_ONLY_KEYS = frozenset({"kitchen_id", "diagnostics_log_dir", "backend_supports_git_write"})

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


# ---------------------------------------------------------------------------
# Stage reachability harness + contract-valid invocation builder (Step 1.1).
# ---------------------------------------------------------------------------
#
# `run_skill` resolves through a deterministic ladder of stages:
#
#   1. orchestrator + enabled gate
#   2. skill_command validation (or resume bypass)
#   3. cwd absoluteness + existence
#   4. ingredient-lock + pipeline-dependency gates
#   5. step-name resolution
#   6. input-contract resolver
#   7. dry-walkthrough gate
#   8. backend/skill resolution + provider override + compat gate
#   9. session construction (init_session + activate_skill_deps)
#  10. executor.run + receipt
#
# Every positive test must prove each predecessor passed; every negative
# test must prove the first denial stage and that all later spies were
# untouched. The harness below materializes a contract-valid invocation
# builder that lays down scalar/integer/file/file-list/directory/optional/
# zero-input contract artifacts in absolute manifest order under `tmp_path`,
# then runs `run_skill` and records which stage produced the first denial.


_RUN_SKILL_STAGES: tuple[str, ...] = (
    "orchestrator_enabled",
    "skill_command_valid",
    "cwd_absolute",
    "ingredient_locks",
    "pipeline_deps",
    "step_name_resolved",
    "input_contracts",
    "dry_walkthrough",
    "backend_compat",
    "session_init",
    "executor_run",
)


@dataclass(frozen=True, slots=True)
class StageTrace:
    """Outcome record for one `run_skill` invocation.

    ``first_denial`` is the name of the earliest stage whose precondition
    failed; None for a positive run. ``reached`` lists every stage that
    passed in order — a negative test asserts the last element of
    ``reached`` matches the expected denial stage. ``response`` is the
    parsed JSON result returned by `run_skill`.
    """

    response: dict[str, Any]
    reached: tuple[str, ...] = ()
    first_denial: str | None = None
    executor_called: bool = False
    init_session_called: bool = False
    activate_skill_deps_called: bool = False

    @property
    def success(self) -> bool:
        return self.response.get("success", False) and self.first_denial is None


def build_contract_valid_invocation(
    tmp_path: Path,
    *,
    skill: str,
    inputs: tuple[tuple[str, str], ...] = (),
    optional_inputs: tuple[tuple[str, str], ...] = (),
    file_inputs: tuple[str, ...] = (),
    file_list_inputs: tuple[tuple[str, tuple[str, ...]], ...] = (),
    directory_inputs: tuple[str, ...] = (),
    integer_inputs: tuple[tuple[str, int], ...] = (),
    closing_issue: str | None = None,
) -> tuple[str, tuple[InputSpec, ...], dict[str, Any]]:
    """Materialize a contract-valid `run_skill` invocation under `tmp_path`.

    Returns ``(skill_command, declared_input_specs, kwargs)`` where
    ``declared_input_specs`` is the absolute-ordered manifest of slots,
    ``kwargs`` is the full mapping to pass to `run_skill`. The artifacts
    (files, directories) live under `tmp_path` so the resolver's path
    validation passes and xdist isolation is preserved.
    """
    parts: list[str] = [skill]
    declared: list[InputSpec] = []
    position = 0
    for name, value in inputs:
        parts.append(value)
        declared.append(
            InputSpec(name=name, type=InputType.STRING, required=True, position=position)
        )
        position += 1
    for name in file_inputs:
        p = tmp_path / f"{name}.txt"
        p.write_text(f"{name} content")
        parts.append(str(p))
        declared.append(
            InputSpec(name=name, type=InputType.FILE_PATH, required=True, position=position)
        )
        position += 1
    for name, paths in file_list_inputs:
        abs_paths: list[str] = []
        for idx in range(len(paths)):
            p = tmp_path / f"{name}_{idx}.txt"
            p.write_text(f"{name}_{idx} content")
            abs_paths.append(str(p))
        parts.append(" ".join(abs_paths))
        declared.append(
            InputSpec(name=name, type=InputType.FILE_PATH_LIST, required=True, position=position)
        )
        position += 1
    for name in directory_inputs:
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        parts.append(str(d))
        declared.append(
            InputSpec(name=name, type=InputType.DIRECTORY_PATH, required=True, position=position)
        )
        position += 1
    for name, value in integer_inputs:
        parts.append(str(value))
        declared.append(
            InputSpec(name=name, type=InputType.INTEGER, required=True, position=position)
        )
        position += 1
    for name, value in optional_inputs:
        parts.append(value)
        declared.append(
            InputSpec(name=name, type=InputType.STRING, required=False, position=position)
        )
        position += 1
    if closing_issue is not None:
        parts.append(closing_issue)
        declared.append(
            InputSpec(
                name="closing_issue", type=InputType.STRING, required=False, position=position
            )
        )
        position += 1

    skill_command = " ".join(parts)
    kwargs: dict[str, Any] = {"cwd": str(tmp_path)}
    return skill_command, tuple(declared), kwargs


def trace_run_skill(result_json: str) -> StageTrace:
    """Construct a :class:`StageTrace` from a `run_skill` JSON response.

    The harness maps the response subtype back onto the deterministic
    stage ladder so callers can assert ``first_denial`` without re-running
    the production gates. Executor/ssm spies are not consulted here;
    callers assert those on the spies directly.
    """
    response = json.loads(result_json) if result_json else {}
    subtype = response.get("subtype", "success")
    if response.get("success") and subtype not in {"crashed", "error_during_execution"}:
        return StageTrace(response=response, reached=_RUN_SKILL_STAGES, executor_called=True)
    # Map known crash subtypes to their stage. Unknown subtypes get
    # ``first_denial="executor_run"`` so callers can still assert exact
    # execution-side responses.
    subtype_to_stage = {
        "dry_walkthrough_gate": "dry_walkthrough",
        "input_contract_invalid": "input_contracts",
        "ingredient_lock_blocked": "ingredient_locks",
        "pipeline_dep_unsatisfied": "pipeline_deps",
        "requires_backend": "backend_compat",
        "unknown_skill": "backend_compat",
        "ambiguous_skill": "backend_compat",
        "invalid_cwd": "cwd_absolute",
        "crashed": "executor_run",
    }
    first_denial = subtype_to_stage.get(subtype, "orchestrator_enabled")
    reached = _RUN_SKILL_STAGES[: _RUN_SKILL_STAGES.index(first_denial)]
    return StageTrace(response=response, reached=reached, first_denial=first_denial)


__all__ = [
    "_simple_prompt_builder",
    "_no_sleep_quota_checker",
    "_noop_quota_refresher",
    "_patch_dispatch_quota_no_sleep",
    "_make_recipe_info",
    "_make_standard_recipe",
    "_skill_ok",
    "_skill_fail",
    "_PATCHED_DEFAULTS",
    "_SERVER_ONLY_KEYS",
    "_MINIMAL_SCRIPT_YAML",
    "StageTrace",
    "build_contract_valid_invocation",
    "trace_run_skill",
]
