"""Formatter field coverage registry for infra test enforcement.

Defines FormatterCoverageDef and _FORMATTER_COVERAGE_REGISTRY — used by
test_all_formatters_have_coverage_contracts and test_coverage_registry_entries_are_valid
in test_pretty_output_hook_infra.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
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
    result["pipeline_tracker"] = {"step": "rectify", "order_id": "test-id", "status": "complete"}
    result["error"] = "DEPENDENCY UNMET: test deny envelope"
    result["stage"] = "preflight:pipeline_deps"
    result["retriable"] = True
    return result


def _dispatch_food_truck_json_producer() -> dict:
    """Return union of all JSON keys from dispatch_food_truck envelope producers.

    Covers DispatchCompleted (default + with optional fields),
    DispatchRejected, and fleet_error() shapes.
    """
    import dataclasses
    import json

    from autoskillit.core import FleetErrorCode, fleet_error
    from autoskillit.fleet.campaign_state.state_effects import DispatchEffectProvenance
    from autoskillit.fleet.campaign_state.state_outcomes import DispatchCompleted, DispatchRejected
    from autoskillit.fleet.campaign_state.state_transitions import DispatchStatus

    provenance = DispatchEffectProvenance(operation_id="formatter-coverage")
    base = DispatchCompleted(
        success=True,
        dispatch_status=DispatchStatus.SUCCESS,
        dispatch_id="d1",
        dispatched_session_id="s1",
        reason="ok",
        effect_provenance=provenance,
    )
    with_optionals = dataclasses.replace(
        base,
        l3_raw_body="raw body text",
        l3_parse_error="parse error text",
        resume_checkpoint={"step": 1, "completed_items": ["a"]},
        health_report={"status": "healthy", "findings": []},
    )
    completed_failure = DispatchCompleted(
        success=False,
        dispatch_status=DispatchStatus.FAILURE,
        dispatch_id="d3",
        dispatched_session_id="s3",
        reason="fleet_l3_no_result_block",
        effect_provenance=provenance,
    )
    rejected = DispatchRejected(
        error_code=FleetErrorCode.FLEET_QUOTA_EXHAUSTED,
        message="quota limit hit",
        details={"limit": 10},
        dispatch_id="d2",
        effect_provenance=provenance,
    )
    error_str = fleet_error(FleetErrorCode.FLEET_ACQUIRE_TIMEOUT, "could not acquire lock")
    partial_bail_envelope = {
        "success": False,
        "error": str(FleetErrorCode.FLEET_RECIPE_INVALID),
        "user_visible_message": "partial bail",
        "details": None,
        "missing_provider_steps": ["fix"],
        "escape_hatch": "Add provider overrides...",
    }
    partial_bail_str = json.dumps(partial_bail_envelope)
    result: dict = {}
    for envelope_str in (
        base.to_envelope(),
        with_optionals.to_envelope(),
        completed_failure.to_envelope(),
        rejected.to_envelope(),
        error_str,
        partial_bail_str,
    ):
        result.update(json.loads(envelope_str))
    return result


def _build_registry() -> dict[str, FormatterCoverageDef]:
    from autoskillit.core.types._type_results import CloneSuccessResult
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _FMT_CLONE_REPO_RENDERED,
        _FMT_CLONE_REPO_SUPPRESSED,
        _FMT_DISPATCH_FOOD_TRUCK_RENDERED,
        _FMT_DISPATCH_FOOD_TRUCK_SUPPRESSED,
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
        DispatchEnvelopeResult,
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
        "dispatch_food_truck": FormatterCoverageDef(
            typed_dict=DispatchEnvelopeResult,
            rendered=_FMT_DISPATCH_FOOD_TRUCK_RENDERED,
            suppressed=_FMT_DISPATCH_FOOD_TRUCK_SUPPRESSED,
            json_producer=_dispatch_food_truck_json_producer,
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


# --- shared check_complexity test infra -----------------------------------------------------
#
# Used by test_check_complexity.py, test_check_complexity_git_e2e.py, and
# test_check_complexity_ruff_parity.py. Each caller keeps its own _CHECK_MODULE_NAME /
# module-loading call site (per-file sys.modules isolation stays a parameter, not shared
# state) -- only the byte-identical bodies are hoisted here.

_CHECK_COMPLEXITY_GIT_TIMEOUT_SECONDS = 30


def load_check_script(name: str, path: Path):
    """Load one gate script fresh via importlib, registering it in sys.modules under *name*.

    Each caller passes its own unique *name* so dataclasses defined in the loaded script
    resolve string annotations through sys.modules[cls.__module__] without colliding with a
    sibling test file's copy of the same script (see
    tests/arch/test_acceptance_policy_relaxation_gate.py).
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        timeout=_CHECK_COMPLEXITY_GIT_TIMEOUT_SECONDS,
    )


def _source_with_function(name: str, complexity: int) -> str:
    """A function with exactly *complexity*, via (complexity - 1) sibling `if` guards."""
    lines = [f"def {name}():"]
    if complexity <= 1:
        lines.append("    pass")
    else:
        for i in range(complexity - 1):
            lines.append(f"    if x{i}:")
            lines.append("        pass")
    return "\n".join(lines) + "\n"


_MINIMAL_LIMITS = "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n"


def _snippet(source: str) -> str:
    return textwrap.dedent(source).strip("\n") + "\n"


# One entry per branch-type construct the counter must recognize; shared so
# test_check_complexity_ruff_parity.py's ruff-parity fixture stays in lockstep with
# test_check_complexity.py's own parametrized counter tests instead of drifting out of sync.
_CONSTRUCT_CASES = [
    ("plain", _snippet("def f():\n    pass\n"), 1),
    ("if", _snippet("def f():\n    if a:\n        pass\n"), 2),
    (
        "if_elif_else",
        _snippet(
            """
            def f():
                if a:
                    pass
                elif b:
                    pass
                else:
                    pass
            """
        ),
        3,
    ),
    (
        "for_else",
        _snippet("def f():\n    for x in y:\n        pass\n    else:\n        pass\n"),
        2,
    ),
    (
        "while_else",
        _snippet("def f():\n    while a:\n        pass\n    else:\n        pass\n"),
        2,
    ),
    (
        "try_2_handlers",
        _snippet(
            """
            def f():
                try:
                    pass
                except A:
                    pass
                except B:
                    pass
            """
        ),
        3,
    ),
    (
        "try_except_else",
        _snippet(
            """
            def f():
                try:
                    pass
                except A:
                    pass
                else:
                    pass
            """
        ),
        3,
    ),
    ("try_finally", _snippet("def f():\n    try:\n        pass\n    finally:\n        pass\n"), 1),
    ("with", _snippet("def f():\n    with a:\n        pass\n"), 1),
    (
        "match_2_literal_plus_wildcard",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case 2:
                        pass
                    case _:
                        pass
            """
        ),
        3,
    ),
    (
        "match_1_literal_plus_name",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case x:
                        pass
            """
        ),
        2,
    ),
    (
        "match_3_literal",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case 2:
                        pass
                    case 3:
                        pass
            """
        ),
        4,
    ),
    (
        "match_guarded_wildcard_last",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case _ if b:
                        pass
            """
        ),
        3,
    ),
    (
        "match_or_irrefutable_last",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case 2 | _:
                        pass
            """
        ),
        2,
    ),
    (
        "match_sequence_as_last",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case [x, y] as w:
                        pass
            """
        ),
        3,
    ),
    (
        "nested_def_with_if",
        _snippet("def f():\n    def g():\n        if a:\n            pass\n"),
        3,
    ),
    ("and_or", _snippet("def f():\n    x = a and b or c\n"), 1),
    ("ternary", _snippet("def f():\n    x = a if b else c\n"), 1),
    ("comprehension_with_filter", _snippet("def f():\n    x = [i for i in y if i]\n"), 1),
    ("assert_stmt", _snippet("def f():\n    assert a\n"), 1),
    ("async_for", _snippet("async def f():\n    async for x in y:\n        pass\n"), 2),
    ("async_with", _snippet("async def f():\n    async with a:\n        pass\n"), 1),
    (
        "class_in_function_method_with_if",
        _snippet(
            """
            def f():
                class C:
                    def m(self):
                        if a:
                            pass
            """
        ),
        3,
    ),
    (
        "except_star_x2",
        _snippet(
            """
            def f():
                try:
                    pass
                except* A:
                    pass
                except* B:
                    pass
            """
        ),
        3,
    ),
    (
        "if_nested_inside_else",
        _snippet(
            """
            def f():
                if a:
                    pass
                else:
                    if b:
                        pass
            """
        ),
        3,
    ),
    (
        "with_body_with_if",
        _snippet("def f():\n    with a:\n        if b:\n            pass\n"),
        2,
    ),
    (
        "try_finally_with_if_in_finally",
        _snippet(
            """
            def f():
                try:
                    pass
                finally:
                    if a:
                        pass
            """
        ),
        2,
    ),
]
