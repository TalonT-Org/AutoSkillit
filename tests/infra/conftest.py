"""Formatter field coverage registry for infra test enforcement.

Defines FormatterCoverageDef and _FORMATTER_COVERAGE_REGISTRY — used by
test_all_formatters_have_coverage_contracts and test_coverage_registry_entries_are_valid
in test_pretty_output_hook_infra.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple


class FormatterCoverageDef(NamedTuple):
    typed_dict: type
    rendered: frozenset[str]
    suppressed: frozenset[str]
    json_producer: Callable[[], dict] | None = None


def _run_skill_json_producer() -> dict:
    """Return union of all JSON keys from SkillResult.to_json() outputs."""
    import dataclasses
    import json

    from autoskillit.core.types._type_results import SkillResult

    r1 = SkillResult.crashed(Exception("test"))
    r2 = dataclasses.replace(r1, worktree_path="/tmp/test-worktree")
    result: dict = {}
    for r in (r1, r2):
        result.update(json.loads(r.to_json()))
    return result


def _build_registry() -> dict[str, FormatterCoverageDef]:
    from autoskillit.core.types._type_results import CloneSuccessResult
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_CLONE_REPO_RENDERED,
        _FMT_CLONE_REPO_SUPPRESSED,
        _FMT_KITCHEN_STATUS_RENDERED,
        _FMT_KITCHEN_STATUS_SUPPRESSED,
        _FMT_LIST_RECIPES_RENDERED,
        _FMT_LIST_RECIPES_SUPPRESSED,
        _FMT_LOAD_RECIPE_RENDERED,
        _FMT_LOAD_RECIPE_SUPPRESSED,
        _FMT_MERGE_WORKTREE_RENDERED,
        _FMT_MERGE_WORKTREE_SUPPRESSED,
        _FMT_OPEN_KITCHEN_RENDERED,
        _FMT_OPEN_KITCHEN_SUPPRESSED,
        _FMT_RUN_CMD_RENDERED,
        _FMT_RUN_CMD_SUPPRESSED,
        _FMT_RUN_SKILL_RENDERED,
        _FMT_RUN_SKILL_SUPPRESSED,
        _FMT_TEST_CHECK_RENDERED,
        _FMT_TEST_CHECK_SUPPRESSED,
        _FMT_TIMING_SUMMARY_RENDERED,
        _FMT_TIMING_SUMMARY_SUPPRESSED,
        _FMT_TOKEN_SUMMARY_RENDERED,
        _FMT_TOKEN_SUMMARY_SUPPRESSED,
    )
    from autoskillit.recipe._api import ListRecipesResult, LoadRecipeResult
    from autoskillit.recipe._recipe_ingredients import OpenKitchenResult
    from autoskillit.server.tools._types import (
        KitchenStatusResult,
        MergeWorktreeResult,
        RunCmdResult,
        RunSkillResult,
        TestCheckResult,
        TimingSummaryResult,
        TokenSummaryResult,
    )

    return {
        "run_skill": FormatterCoverageDef(
            typed_dict=RunSkillResult,
            rendered=_FMT_RUN_SKILL_RENDERED,
            suppressed=_FMT_RUN_SKILL_SUPPRESSED,
            json_producer=_run_skill_json_producer,
        ),
        "run_cmd": FormatterCoverageDef(
            typed_dict=RunCmdResult,
            rendered=_FMT_RUN_CMD_RENDERED,
            suppressed=_FMT_RUN_CMD_SUPPRESSED,
        ),
        "test_check": FormatterCoverageDef(
            typed_dict=TestCheckResult,
            rendered=_FMT_TEST_CHECK_RENDERED,
            suppressed=_FMT_TEST_CHECK_SUPPRESSED,
        ),
        "merge_worktree": FormatterCoverageDef(
            typed_dict=MergeWorktreeResult,
            rendered=_FMT_MERGE_WORKTREE_RENDERED,
            suppressed=_FMT_MERGE_WORKTREE_SUPPRESSED,
        ),
        "get_token_summary": FormatterCoverageDef(
            typed_dict=TokenSummaryResult,
            rendered=_FMT_TOKEN_SUMMARY_RENDERED,
            suppressed=_FMT_TOKEN_SUMMARY_SUPPRESSED,
        ),
        "get_timing_summary": FormatterCoverageDef(
            typed_dict=TimingSummaryResult,
            rendered=_FMT_TIMING_SUMMARY_RENDERED,
            suppressed=_FMT_TIMING_SUMMARY_SUPPRESSED,
        ),
        "kitchen_status": FormatterCoverageDef(
            typed_dict=KitchenStatusResult,
            rendered=_FMT_KITCHEN_STATUS_RENDERED,
            suppressed=_FMT_KITCHEN_STATUS_SUPPRESSED,
        ),
        "clone_repo": FormatterCoverageDef(
            typed_dict=CloneSuccessResult,
            rendered=_FMT_CLONE_REPO_RENDERED,
            suppressed=_FMT_CLONE_REPO_SUPPRESSED,
        ),
        "load_recipe": FormatterCoverageDef(
            typed_dict=LoadRecipeResult,
            rendered=_FMT_LOAD_RECIPE_RENDERED,
            suppressed=_FMT_LOAD_RECIPE_SUPPRESSED,
        ),
        "open_kitchen": FormatterCoverageDef(
            typed_dict=OpenKitchenResult,
            rendered=_FMT_OPEN_KITCHEN_RENDERED,
            suppressed=_FMT_OPEN_KITCHEN_SUPPRESSED,
        ),
        "list_recipes": FormatterCoverageDef(
            typed_dict=ListRecipesResult,
            rendered=_FMT_LIST_RECIPES_RENDERED,
            suppressed=_FMT_LIST_RECIPES_SUPPRESSED,
        ),
    }


_FORMATTER_COVERAGE_REGISTRY: dict[str, FormatterCoverageDef] | None = None


def _get_formatter_coverage_registry() -> dict[str, FormatterCoverageDef]:
    global _FORMATTER_COVERAGE_REGISTRY  # noqa: PLW0603
    if _FORMATTER_COVERAGE_REGISTRY is None:
        _FORMATTER_COVERAGE_REGISTRY = _build_registry()
    return _FORMATTER_COVERAGE_REGISTRY
