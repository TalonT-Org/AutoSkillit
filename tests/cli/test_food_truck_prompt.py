"""Group E tests: L3 food truck prompt builder — sous-chef subset, inversions,
budget guidance, quota awareness, sentinel format."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from autoskillit.core.types import CaptureEntrySpec, resolve_payload_field

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]

# --- Fixtures ---

_RECIPE = "test-recipe"
_TASK = "implement feature X"
_INGREDIENTS = {"branch": "main", "issue_url": "https://github.com/org/repo/issues/1"}
_MCP_PREFIX = "mcp__autoskillit__"
_DISPATCH_ID = "abc12345deadbeef"
_DISPATCH_ID_SHORT = "abc12345"
_CAMPAIGN_ID = "camp-001"
_L3_TIMEOUT = 3600


def _get_prompt() -> str:
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    return _build_food_truck_prompt(
        recipe=_RECIPE,
        task=_TASK,
        ingredients=_INGREDIENTS,
        mcp_prefix=_MCP_PREFIX,
        dispatch_id=_DISPATCH_ID,
        campaign_id=_CAMPAIGN_ID,
        l3_timeout_sec=_L3_TIMEOUT,
    )


def _get_admiral_block() -> str:
    from autoskillit.fleet._prompts import _build_admiral_dispatch_block

    return _build_admiral_dispatch_block()


# --- Group E-1: Sous-Chef Subset Filter ---


class TestAdmiralDispatchBlock:
    def test_retains_exactly_four_sections(self) -> None:
        block = _get_admiral_block()
        for title in (
            "CONTEXT LIMIT ROUTING",
            "STEP NAME IMMUTABILITY",
            "MERGE PHASE",
            "QUOTA WAIT PROTOCOL",
        ):
            assert title in block, f"Missing retained section: {title}"
        assert len(re.findall(r"^## ", block, re.MULTILINE)) == 4

    def test_excludes_five_sections(self) -> None:
        block = _get_admiral_block()
        for title in (
            "MULTI-PART PLAN SEQUENCING",
            "AUDIT-IMPL ACROSS MULTI-GROUP",
            "READING AND ACTING ON",
            "MULTIPLE ISSUES",
            "PARALLEL STEP SCHEDULING",
        ):
            assert title not in block, f"Excluded section present: {title}"

    def test_no_dangling_crossrefs(self) -> None:
        block = _get_admiral_block()
        for phrase in (
            "MULTI-PART PLAN",
            "AUDIT-IMPL",
            "plan_parts=",
            "MULTIPLE ISSUES",
            "PARALLEL STEP",
            "wavefront",
        ):
            assert phrase not in block, f"Dangling crossref: {phrase}"

    def test_graceful_degradation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        from autoskillit.fleet import _prompts as _fleet_prompts

        monkeypatch.setattr(_fleet_prompts, "pkg_root", lambda: tmp_path)
        result = _fleet_prompts._build_admiral_dispatch_block()
        assert result == ""

    def test_excludes_unretained_sections(self) -> None:
        block = _get_admiral_block()
        for title in (
            "SKILL_COMMAND FORMATTING",
            "STEP EXECUTION IS NOT DISCRETIONARY",
            "NARRATION SUPPRESSION",
        ):
            assert f"## {title}" not in block, f"Unretained section present: {title}"

    def test_l3_sections_constant_matches_build_output(self) -> None:
        from autoskillit.core.types._type_constants import (
            ADMIRAL_DISPATCH_SECTIONS,
            SOUS_CHEF_MANDATORY_SECTIONS,
        )

        assert set(ADMIRAL_DISPATCH_SECTIONS).issubset(set(SOUS_CHEF_MANDATORY_SECTIONS))
        block = _get_admiral_block()
        for header in ADMIRAL_DISPATCH_SECTIONS:
            assert f"## {header}" in block, (
                f"_build_admiral_dispatch_block() missing dispatch section: {header!r}. "
                "Update ADMIRAL_DISPATCH_SECTIONS or the allowlist in fleet/_prompts.py."
            )
        extra = set(SOUS_CHEF_MANDATORY_SECTIONS) - set(ADMIRAL_DISPATCH_SECTIONS)
        for header in extra:
            assert f"## {header}" not in block, (
                f"_build_admiral_dispatch_block() unexpectedly includes "
                f"non-dispatch section: {header!r}"
            )


# --- Group E-2: Placeholder Interpolation ---


class TestFoodTruckPromptPlaceholders:
    def test_interpolates_all_placeholders(self) -> None:
        prompt = _get_prompt()
        assert _RECIPE in prompt
        assert _TASK in prompt
        assert _DISPATCH_ID in prompt
        assert _DISPATCH_ID_SHORT in prompt
        assert _CAMPAIGN_ID in prompt
        assert str(_L3_TIMEOUT) in prompt
        assert _MCP_PREFIX in prompt
        assert json.dumps(_INGREDIENTS) in prompt
        assert '"branch": "main"' in prompt


# --- Group E-3: Headless Mode Inversions ---


class TestHeadlessModeDirectives:
    def test_h1_open_kitchen_with_overrides(self) -> None:
        prompt = _get_prompt()
        assert "open_kitchen" in prompt
        assert "overrides=" in prompt
        assert f"{_MCP_PREFIX}open_kitchen" in prompt

    def test_h2_no_ingredient_prompting(self) -> None:
        prompt = _get_prompt()
        assert "DO NOT prompt for ingredient" in prompt
        assert "Collect ingredient values" not in prompt

    def test_h3_auto_accept_confirm(self) -> None:
        prompt = _get_prompt()
        assert "AUTO-ACCEPT CONFIRM STEPS" in prompt
        assert "AskUserQuestion with the step's message" not in prompt

    def test_h4_ask_user_question_auto_accepts(self) -> None:
        prompt = _get_prompt()
        assert "AskUserQuestion" in prompt
        assert "auto-accept" in prompt.lower() or "auto_accept" in prompt.lower()

    def test_h5_run_cmd_required(self) -> None:
        prompt = _get_prompt()
        assert "run_cmd" in prompt

    def test_h6_sentinel_only_output(self) -> None:
        prompt = _get_prompt()
        assert "SENTINEL-ONLY OUTPUT" in prompt
        assert "Display it verbatim" not in prompt


# --- Group E-4: Section Content ---


class TestFoodTruckPromptSections:
    def test_contains_routing_rules(self) -> None:
        prompt = _get_prompt()
        assert "ROUTING RULES" in prompt
        assert "on_failure" in prompt

    def test_contains_failure_predicates(self) -> None:
        prompt = _get_prompt()
        assert "FAILURE PREDICATES" in prompt
        for tool in ("test_check", "merge_worktree", "run_cmd", "run_skill"):
            assert tool in prompt

    def test_contains_budget_guidance(self) -> None:
        prompt = _get_prompt()
        assert "BUDGET" in prompt
        assert "120 seconds" in prompt
        assert "900 seconds" in prompt
        assert "1800 seconds" in prompt

    def test_contains_quota_awareness(self) -> None:
        prompt = _get_prompt()
        assert "quota_exhausted" in prompt
        assert "wait_seconds" in prompt

    def test_contains_campaign_task_block(self) -> None:
        prompt = _get_prompt()
        assert "CAMPAIGN TASK" in prompt
        assert _RECIPE in prompt
        assert _TASK in prompt

    def test_contains_ingredient_values_block(self) -> None:
        prompt = _get_prompt()
        assert "INGREDIENT VALUES" in prompt
        assert '"branch"' in prompt


# --- Group E-5: Sentinel Format ---


class TestSentinelFormat:
    def test_sentinel_format_dispatch_id(self) -> None:
        prompt = _get_prompt()
        assert f"---l3-result::{_DISPATCH_ID}---" in prompt
        assert f"---end-l3-result::{_DISPATCH_ID}---" in prompt

    def test_sentinel_done_marker(self) -> None:
        prompt = _get_prompt()
        assert f"%%L3_DONE::{_DISPATCH_ID_SHORT}%%" in prompt

    def test_sentinel_json_body_contract(self) -> None:
        prompt = _get_prompt()
        assert '"success"' in prompt
        assert '"reason"' in prompt

    @pytest.mark.parametrize(
        "capture_arg",
        [
            {
                "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
            },
            {
                "worktree_path": CaptureEntrySpec(from_="${{ result.worktree_path }}"),
                "pr_url": CaptureEntrySpec(from_="${{ result.pr_url }}"),
            },
        ],
        ids=["1-key", "2-key"],
    )
    def test_sentinel_capture_fields_injected(
        self, capture_arg: dict[str, CaptureEntrySpec]
    ) -> None:
        from autoskillit.fleet._prompts import _build_food_truck_prompt

        prompt = _build_food_truck_prompt(
            recipe=_RECIPE,
            task=_TASK,
            ingredients=_INGREDIENTS,
            mcp_prefix=_MCP_PREFIX,
            dispatch_id=_DISPATCH_ID,
            campaign_id=_CAMPAIGN_ID,
            l3_timeout_sec=_L3_TIMEOUT,
            capture=capture_arg,
        )
        section8 = prompt[prompt.index("--- SECTION 8") :]
        for entry in capture_arg.values():
            field = resolve_payload_field(entry)
            assert field is not None
            assert f'"{field}"' in section8
            assert entry.from_ in section8
        assert '"success"' in section8
        assert '"reason"' in section8

    @pytest.mark.parametrize("capture_arg", [None, {}], ids=["none", "empty"])
    def test_sentinel_section8_unchanged_when_capture_none(
        self, capture_arg: dict[str, CaptureEntrySpec] | None
    ) -> None:
        from autoskillit.fleet._prompts import _build_food_truck_prompt

        prompt = _build_food_truck_prompt(
            recipe=_RECIPE,
            task=_TASK,
            ingredients=_INGREDIENTS,
            mcp_prefix=_MCP_PREFIX,
            dispatch_id=_DISPATCH_ID,
            campaign_id=_CAMPAIGN_ID,
            l3_timeout_sec=_L3_TIMEOUT,
            capture=capture_arg,
        )
        section8 = prompt[prompt.index("--- SECTION 8") :]
        assert "capture_" not in section8


# --- Group E-6: No First-Action Bootstrap ---


class TestNoBootstrapSequence:
    def test_no_bash_sleep_step(self) -> None:
        prompt = _get_prompt()
        assert 'Bash(command="sleep 2")' not in prompt

    def test_no_toolsearch_bootstrap(self) -> None:
        prompt = _get_prompt()
        assert "ToolSearch(query='select:" not in prompt
