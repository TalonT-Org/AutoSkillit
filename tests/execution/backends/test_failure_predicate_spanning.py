from __future__ import annotations

import inspect
import re
from typing import get_type_hints

import pytest

from autoskillit.cli._prompts_orchestrator import _build_orchestrator_prompt
from autoskillit.fleet._prompts import _build_food_truck_prompt
from autoskillit.server.tools._types import (
    MergeWorktreeResult,
    RunCmdResult,
    RunSkillResult,
    TestCheckResult,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# ── Shared constants ──────────────────────────────────────────────

_TOOL_TYPEDDICT_MAP: dict[str, type] = {
    "test_check": TestCheckResult,
    "merge_worktree": MergeWorktreeResult,
    "run_cmd": RunCmdResult,
    "run_skill": RunSkillResult,
}

_EXPECTED_FIELD_MAP: dict[str, str] = {
    "test_check": "passed",
    "merge_worktree": "error",
    "run_cmd": "success",
    "run_skill": "success",
}

_ALL_TOOL_NAMES = ["test_check", "merge_worktree", "run_cmd", "run_skill", "classify_fix"]


# ── Shared helper ─────────────────────────────────────────────────


def _extract_predicate_block(source_text: str) -> str:
    """Locate the 'FAILURE PREDICATES' section dynamically and extract it.

    Searches for the header line containing 'FAILURE PREDICATES — when to follow'
    and extracts all subsequent '- tool_name:' lines until a blank line or
    next section boundary.
    """
    match = re.search(
        r"(FAILURE PREDICATES — when to follow on_failure:.*?)(?=\n\n|\n[A-Z])",
        source_text,
        re.DOTALL,
    )
    assert match is not None, (
        "Could not find 'FAILURE PREDICATES — when to follow on_failure:' section"
    )
    return match.group(1)


def _extract_open_kitchen_predicate_block(rendered_text: str) -> str:
    """Extract the 'FAILURE PREDICATE — open_kitchen:' block from rendered prompt."""
    match = re.search(
        r"(FAILURE PREDICATE — open_kitchen:.*?)(?=\n\n|\nFAILURE PREDICATE —[^o]|\Z)",
        rendered_text,
        re.DOTALL,
    )
    assert match is not None, "Could not find 'FAILURE PREDICATE — open_kitchen:' section"
    return match.group(1)


def _parse_predicate_lines(block: str) -> dict[str, str]:
    """Parse '- tool_name: ...' lines from a predicate block into a dict."""
    lines: dict[str, str] = {}
    for m in re.finditer(r"^- (\w+): (.+)$", block, re.MULTILINE):
        lines[m.group(1)] = m.group(0)
    return lines


# ── Stub helpers ──────────────────────────────────────────────────


def _render_orchestrator() -> str:
    return _build_orchestrator_prompt(
        recipe_name="stub-recipe",
        mcp_prefix="mcp__stub",
    )


def _render_food_truck() -> str:
    return _build_food_truck_prompt(
        recipe="stub-recipe",
        task="stub task",
        ingredients={},
        mcp_prefix="mcp__stub",
        dispatch_id="d-00000000",
        campaign_id="c-00000000",
        l3_timeout_sec=300,
    )


# ── Test classes ──────────────────────────────────────────────────


class TestToolPredicateFieldsMatchSchema:
    """Validate each FAILURE PREDICATE field name against its TypedDict schema."""

    @pytest.mark.parametrize(
        "builder_fn",
        [_build_orchestrator_prompt, _build_food_truck_prompt],
        ids=["orchestrator", "food_truck"],
    )
    @pytest.mark.parametrize("tool_name", list(_TOOL_TYPEDDICT_MAP.keys()))
    def test_predicate_field_in_typeddict(self, tool_name: str, builder_fn: object) -> None:
        source = inspect.getsource(builder_fn)
        block = _extract_predicate_block(source)
        parsed = _parse_predicate_lines(block)

        assert tool_name in parsed, f"{tool_name} not found in predicate block"

        expected_field = _EXPECTED_FIELD_MAP[tool_name]
        td_cls = _TOOL_TYPEDDICT_MAP[tool_name]
        valid_keys = set(get_type_hints(td_cls))

        assert expected_field in valid_keys, (
            f"Predicate field '{expected_field}' for {tool_name} "
            f"is not a key in {td_cls.__name__}. Valid keys: {sorted(valid_keys)}"
        )

        line_text = parsed[tool_name]
        assert f'"{expected_field}:' in line_text or f'"{expected_field}"' in line_text, (
            f"Expected field '{expected_field}' not found in predicate line: {line_text}"
        )

    @pytest.mark.parametrize(
        "builder_fn",
        [_build_orchestrator_prompt, _build_food_truck_prompt],
        ids=["orchestrator", "food_truck"],
    )
    def test_classify_fix_contains_error_colon(self, builder_fn: object) -> None:
        source = inspect.getsource(builder_fn)
        block = _extract_predicate_block(source)
        parsed = _parse_predicate_lines(block)

        assert "classify_fix" in parsed, "classify_fix not found in predicate block"
        assert "error:" in parsed["classify_fix"], (
            f"classify_fix predicate does not contain 'error:': {parsed['classify_fix']}"
        )

    @pytest.mark.parametrize(
        "builder_fn",
        [_build_orchestrator_prompt, _build_food_truck_prompt],
        ids=["orchestrator", "food_truck"],
    )
    def test_predicate_block_contains_all_tools(self, builder_fn: object) -> None:
        source = inspect.getsource(builder_fn)
        block = _extract_predicate_block(source)
        for tool_name in _ALL_TOOL_NAMES:
            assert tool_name in block, f"Tool '{tool_name}' missing from FAILURE PREDICATES block"


class TestOpenKitchenIngredientTableMarker:
    """POST-C1 regression guard: open_kitchen predicate must reference JSON fields,
    not the hook-injected display marker."""

    def test_no_ingredients_table_marker(self) -> None:
        rendered = _render_food_truck()
        block = _extract_open_kitchen_predicate_block(rendered)
        assert "--- INGREDIENTS TABLE ---" not in block, (
            "open_kitchen FAILURE PREDICATE must not reference the hook-injected "
            "'--- INGREDIENTS TABLE ---' marker"
        )

    def test_references_success_and_ingredients_table(self) -> None:
        rendered = _render_food_truck()
        block = _extract_open_kitchen_predicate_block(rendered)
        assert "success" in block, (
            "open_kitchen predicate block must reference 'success' as a JSON field"
        )
        assert "ingredients_table" in block, (
            "open_kitchen predicate block must reference 'ingredients_table' as a JSON field"
        )


class TestPredicateParity:
    """Assert the five shared tool predicate lines are character-identical
    between orchestrator and food truck builders."""

    def test_shared_predicate_lines_character_identical(self) -> None:
        orch_rendered = _render_orchestrator()
        ft_rendered = _render_food_truck()

        orch_block = _extract_predicate_block(orch_rendered)
        ft_block = _extract_predicate_block(ft_rendered)

        orch_lines = _parse_predicate_lines(orch_block)
        ft_lines = _parse_predicate_lines(ft_block)

        for tool_name in _ALL_TOOL_NAMES:
            assert tool_name in orch_lines, (
                f"{tool_name} missing from orchestrator predicate block"
            )
            assert tool_name in ft_lines, f"{tool_name} missing from food truck predicate block"
            assert orch_lines[tool_name] == ft_lines[tool_name], (
                f"Predicate parity violation for {tool_name}:\n"
                f"  orchestrator: {orch_lines[tool_name]!r}\n"
                f"  food_truck:   {ft_lines[tool_name]!r}"
            )

    def test_predicate_extraction_is_dynamic(self) -> None:
        """Verify extraction works on both source text and rendered output,
        proving it searches by header not by hardcoded offsets."""
        orch_source = inspect.getsource(_build_orchestrator_prompt)
        ft_source = inspect.getsource(_build_food_truck_prompt)

        for source in [orch_source, ft_source]:
            block = _extract_predicate_block(source)
            assert "FAILURE PREDICATES" in block
            for tool_name in _ALL_TOOL_NAMES:
                assert tool_name in block

        for rendered in [_render_orchestrator(), _render_food_truck()]:
            block = _extract_predicate_block(rendered)
            assert "FAILURE PREDICATES" in block
            for tool_name in _ALL_TOOL_NAMES:
                assert tool_name in block
