"""Group K tests: L3 campaign dispatcher prompt builder — 10 sections, tool surface,
sentinel format, progress markers, and negative bootstrap assertions."""

from __future__ import annotations

import pathlib

import pytest

from autoskillit.cli._mcp_names import DIRECT_PREFIX, MARKETPLACE_PREFIX
from autoskillit.core.types._type_constants import SOUS_CHEF_MANDATORY_SECTIONS
from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]

# --- Constants ---

_CAMPAIGN_ID = "campaign-test-1234-5678"
_MAX_QUOTA_WAIT = 3600
_MANIFEST_YAML = """\
dispatches:
  - name: phase-1
    recipe: implementation
    task: "Build the thing"
  - name: phase-2
    recipe: implementation
    task: "Test the thing"
    depends_on: [phase-1]
"""


def _make_recipe() -> Recipe:
    return Recipe(
        name="test-campaign",
        description="A test campaign for validation",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[
            CampaignDispatch(name="phase-1", recipe="implementation", task="Build the thing"),
            CampaignDispatch(
                name="phase-2",
                recipe="implementation",
                task="Test the thing",
                depends_on=["phase-1"],
            ),
        ],
        continue_on_failure=False,
    )


def _build(**overrides: object) -> str:
    from autoskillit.cli._prompts import _build_fleet_campaign_prompt

    defaults: dict[str, object] = {
        "campaign_recipe": _make_recipe(),
        "manifest_yaml": _MANIFEST_YAML,
        "completed_dispatches": "",
        "mcp_prefix": DIRECT_PREFIX,
        "campaign_id": _CAMPAIGN_ID,
        "max_quota_wait_sec": _MAX_QUOTA_WAIT,
    }
    defaults.update(overrides)
    return _build_fleet_campaign_prompt(**defaults)  # type: ignore[arg-type]


# --- K-1: TestL3PromptPlaceholders ---


class TestL3PromptPlaceholders:
    def test_all_parameters_interpolated(self) -> None:
        prompt = _build()
        assert _CAMPAIGN_ID in prompt
        assert "test-campaign" in prompt
        assert "A test campaign for validation" in prompt
        assert "2 dispatches" in prompt
        assert DIRECT_PREFIX in prompt
        assert str(_MAX_QUOTA_WAIT) in prompt
        assert _MANIFEST_YAML.strip() in prompt


# --- K-2: TestL3SousChefDiscipline ---


class TestL3SousChefDiscipline:
    def test_full_sous_chef_appended(self) -> None:
        prompt = _build()
        for header in SOUS_CHEF_MANDATORY_SECTIONS:
            assert header in prompt, f"Missing sous-chef section: {header}"

    def test_graceful_degradation_on_missing_skill_md(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        from autoskillit.cli import _prompts

        monkeypatch.setattr(_prompts, "pkg_root", lambda: tmp_path)
        result = _build()
        assert isinstance(result, str)
        assert len(result) > 0
        # Non-sous-chef structural sections must survive degradation
        assert "CAMPAIGN OVERVIEW" in result
        assert "DISPATCH MANIFEST" in result
        assert "FAILURE RECOVERY" in result
        assert "QUOTA RETRY" in result
        assert "INTERRUPT/CLEANUP SEQUENCE" in result


# --- K-3: TestCampaignOverviewSection ---


class TestCampaignOverviewSection:
    def test_campaign_overview_present(self) -> None:
        prompt = _build()
        assert "CAMPAIGN OVERVIEW" in prompt

    def test_overview_contains_name_id_description_count(self) -> None:
        prompt = _build()
        assert "test-campaign" in prompt
        assert _CAMPAIGN_ID in prompt
        assert "A test campaign for validation" in prompt
        assert "2 dispatches" in prompt


# --- K-4: TestDispatchManifestSection ---


class TestDispatchManifestSection:
    def test_manifest_yaml_embedded_verbatim(self) -> None:
        prompt = _build()
        assert _MANIFEST_YAML.strip() in prompt

    def test_dispatch_manifest_header_present(self) -> None:
        prompt = _build()
        assert "DISPATCH MANIFEST" in prompt


# --- K-5: TestCampaignDisciplineSection ---


class TestCampaignDisciplineSection:
    def test_dispatch_food_truck_referenced(self) -> None:
        prompt = _build()
        assert f"{DIRECT_PREFIX}dispatch_food_truck" in prompt

    def test_no_parallel_dispatch(self) -> None:
        prompt = _build()
        assert "serial" in prompt.lower() or "sequential" in prompt.lower()

    def test_no_cross_dispatch_aggregation(self) -> None:
        prompt = _build()
        assert "cross-dispatch token aggregation" in prompt or "NO cross-dispatch" in prompt

    def test_fleet_lock_mentioned(self) -> None:
        prompt = _build()
        assert "fleet_lock" in prompt


# --- K-6: TestFailureRecoverySection ---


class TestFailureRecoverySection:
    def test_failure_decision_tree_header(self) -> None:
        prompt = _build()
        assert "FAILURE RECOVERY" in prompt

    def test_three_failure_conditions(self) -> None:
        prompt = _build()
        assert "success=false" in prompt
        assert "null" in prompt
        assert ".success=false" in prompt

    def test_continue_on_failure_referenced(self) -> None:
        prompt = _build()
        assert "continue_on_failure" in prompt

    def test_no_retry_in_v1(self) -> None:
        prompt = _build()
        assert "NEVER retry" in prompt or "never retry" in prompt.lower()


# --- K-7: TestQuotaRetrySection ---


class TestQuotaRetrySection:
    def test_quota_retry_header(self) -> None:
        prompt = _build()
        assert "QUOTA RETRY" in prompt

    def test_quota_exhausted_trigger(self) -> None:
        prompt = _build()
        assert "quota_exhausted" in prompt

    def test_max_quota_wait_sec_interpolated(self) -> None:
        prompt = _build(max_quota_wait_sec=7200)
        assert "7200" in prompt

    def test_retry_once_then_halt(self) -> None:
        prompt = _build()
        assert "ONCE" in prompt or "once" in prompt.lower()
        assert "halt" in prompt.lower()


# --- K-8: TestResumeStateInjection ---


class TestResumeStateInjection:
    def test_completed_dispatches_block_present_when_supplied(self) -> None:
        completed = "- phase-1: SUCCESS\n"
        prompt = _build(completed_dispatches=completed)
        assert "COMPLETED DISPATCHES" in prompt
        assert "phase-1: SUCCESS" in prompt

    def test_completed_dispatches_block_absent_when_empty(self) -> None:
        prompt = _build(completed_dispatches="")
        assert "COMPLETED DISPATCHES" not in prompt

    def test_do_not_re_dispatch_instruction(self) -> None:
        completed = "- phase-1: SUCCESS\n"
        prompt = _build(completed_dispatches=completed)
        assert "DO NOT RE-DISPATCH" in prompt


class TestResumableSessionIdInjection:
    def test_resumable_section_includes_resume_session_id_instruction(self) -> None:
        prompt = _build(
            resumable_dispatch_name="impl-1",
            resumable_session_id="sess-abc-456",
        )
        assert "RESUMABLE DISPATCH: impl-1" in prompt
        assert 'resume_session_id="sess-abc-456"' in prompt

    def test_resumable_section_without_session_id_omits_instruction(self) -> None:
        prompt = _build(
            resumable_dispatch_name="impl-1",
            resumable_session_id="",
        )
        assert "RESUMABLE DISPATCH: impl-1" in prompt
        assert "resume_session_id" not in prompt

    def test_no_resumable_section_when_dispatch_name_empty(self) -> None:
        prompt = _build(resumable_dispatch_name="", resumable_session_id="sess-abc")
        assert "RESUMABLE DISPATCH" not in prompt


# --- K-9: TestInterruptCleanupSection ---


class TestInterruptCleanupSection:
    def test_interrupt_cleanup_header(self) -> None:
        prompt = _build()
        assert "INTERRUPT/CLEANUP SEQUENCE" in prompt

    def test_batch_cleanup_clones_called_first(self) -> None:
        prompt = _build()
        cleanup_idx = prompt.index("batch_cleanup_clones")
        summary_idx = prompt.index("campaign summary")
        assert cleanup_idx < summary_idx

    def test_cleanup_sequence_order(self) -> None:
        prompt = _build()
        cleanup_idx = prompt.index("batch_cleanup_clones")
        summary_idx = prompt.lower().index("campaign summary")
        end_idx = prompt.lower().index("end the session")
        assert cleanup_idx < summary_idx < end_idx


# --- K-10: TestCampaignSummaryContract ---


class TestCampaignSummaryContract:
    def test_campaign_summary_sentinels(self) -> None:
        prompt = _build()
        assert f"---campaign-summary::{_CAMPAIGN_ID}---" in prompt
        assert f"---end-campaign-summary::{_CAMPAIGN_ID}---" in prompt

    def test_per_dispatch_field_present(self) -> None:
        prompt = _build()
        assert "per_dispatch" in prompt

    def test_error_records_field_present(self) -> None:
        prompt = _build()
        assert "error_records" in prompt

    def test_no_aggregate_fields(self) -> None:
        prompt = _build()
        # Extract just the campaign summary schema section to avoid false positives
        # from sous-chef content that legitimately uses the word "aggregate".
        start = prompt.index(f"---campaign-summary::{_CAMPAIGN_ID}---")
        end = prompt.index(f"---end-campaign-summary::{_CAMPAIGN_ID}---") + len(
            f"---end-campaign-summary::{_CAMPAIGN_ID}---"
        )
        summary_section = prompt[start:end]
        assert "total_tokens" not in summary_section
        assert "aggregate" not in summary_section
        assert "total_input_tokens" not in summary_section
        assert "total_output_tokens" not in summary_section


# --- K-11: TestProgressMarkers ---


class TestProgressMarkers:
    def test_progress_marker_format(self) -> None:
        prompt = _build()
        assert f"%%FLEET_PROGRESS::{_CAMPAIGN_ID}::" in prompt

    def test_all_state_transitions_listed(self) -> None:
        prompt = _build()
        for state in ("queued", "running", "success", "failure", "skipped"):
            assert state in prompt, f"Missing progress state: {state}"

    def test_dispatch_index_placeholder(self) -> None:
        prompt = _build()
        assert "dispatch_<i>_of_<n>" in prompt


# --- K-12: TestToolSurface ---


class TestToolSurface:
    @pytest.mark.parametrize("prefix", [DIRECT_PREFIX, MARKETPLACE_PREFIX])
    def test_six_fleet_tools_listed(self, prefix: str) -> None:
        prompt = _build(mcp_prefix=prefix)
        for tool in (
            "dispatch_food_truck",
            "batch_cleanup_clones",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
        ):
            assert f"{prefix}{tool}" in prompt, f"Missing tool: {prefix}{tool}"

    def test_open_kitchen_in_forbidden_list(self) -> None:
        """open_kitchen, close_kitchen, and run_skill are all forbidden in fleet sessions."""
        prompt = _build()
        forbidden_idx = prompt.index("Explicitly FORBIDDEN")
        forbidden_line = prompt[forbidden_idx : prompt.index(chr(10), forbidden_idx)]
        assert "open_kitchen" in forbidden_line
        assert "close_kitchen" in forbidden_line
        assert "run_skill" in forbidden_line

    def test_uses_dispatch_food_truck_not_run_skill(self) -> None:
        prompt = _build()
        assert f"{DIRECT_PREFIX}dispatch_food_truck" in prompt
        # run_skill must not be described as the dispatch mechanism
        assert "dispatch via run_skill" not in prompt
        assert "dispatch through run_skill" not in prompt


# --- K-13: TestL3NoBootstrapSequence ---


class TestL3NoBootstrapSequence:
    """K-13 (T6): Fleet auto-gate — no open_kitchen startup sequence needed."""

    def test_no_startup_sequence_section(self) -> None:
        prompt = _build()
        assert "STARTUP SEQUENCE" not in prompt

    def test_no_bash_sleep(self) -> None:
        prompt = _build()
        assert 'Bash(command="sleep 2")' not in prompt

    def test_no_open_kitchen_toolsearch(self) -> None:
        prompt = _build()
        assert "ToolSearch(query='select:" not in prompt

    def test_open_kitchen_not_callable_in_prompt(self) -> None:
        prompt = _build()
        assert f"{DIRECT_PREFIX}open_kitchen()" not in prompt

    @pytest.mark.parametrize("prefix", [DIRECT_PREFIX, MARKETPLACE_PREFIX])
    def test_open_kitchen_not_callable_for_any_prefix(self, prefix: str) -> None:
        prompt = _build(mcp_prefix=prefix)
        assert f"{prefix}open_kitchen()" not in prompt


# --- T8-12: TestFleetCampaignRoleText ---


class TestFleetCampaignRoleText:
    def test_role_identifies_as_fleet_campaign_dispatcher(self) -> None:
        prompt = _build()
        assert "fleet campaign dispatcher" in prompt


# --- K-14: TestDynamicDispatchSection ---


class TestDynamicDispatchSection:
    def _make_dynamic_recipe(self) -> Recipe:
        return Recipe(
            name="audit-campaign",
            description="Full audit and implement campaign",
            kind=RecipeKind.CAMPAIGN,
            dispatches=[
                CampaignDispatch(
                    name="build-map",
                    recipe="bem-wrapper",
                    task="Build execution map",
                    capture={
                        "execution_map": "${{ result.execution_map }}",
                        "dispatch_plan": "${{ result.dispatch_plan }}",
                    },
                ),
            ],
            continue_on_failure=False,
        )

    def test_dynamic_dispatch_section_present_when_dispatch_plan_captured(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "DYNAMIC DISPATCH" in prompt

    def test_dynamic_dispatch_section_absent_for_static_only_campaign(self) -> None:
        prompt = _build()
        assert "DYNAMIC DISPATCH" not in prompt

    def test_implement_findings_recipe_name_referenced(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "implement-findings" in prompt

    def test_dispatch_plan_campaign_ref_referenced(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "campaign.dispatch_plan" in prompt

    def test_group_iteration_instruction_present(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "For each group (in array order)" in prompt

    def test_naming_convention_instruction_present(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "-g" in prompt and "-a" in prompt

    def test_parallel_dispatch_instruction_present(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "parallel tool calls" in prompt

    def test_wait_for_group_before_next_group(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "Wait for ALL food trucks in this group" in prompt

    def test_max_issues_per_food_truck_split_instruction(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "batches of that size" in prompt

    def test_empty_dispatch_plan_handled(self) -> None:
        prompt = _build(campaign_recipe=self._make_dynamic_recipe())
        assert "no issues to implement" in prompt

    def test_dynamic_dispatch_absent_when_only_execution_map_captured(self) -> None:
        recipe = Recipe(
            name="partial-campaign",
            description="Only captures execution_map",
            kind=RecipeKind.CAMPAIGN,
            dispatches=[
                CampaignDispatch(
                    name="build-map",
                    recipe="bem-wrapper",
                    task="Build map",
                    capture={"execution_map": "${{ result.execution_map }}"},
                ),
            ],
            continue_on_failure=False,
        )
        prompt = _build(campaign_recipe=recipe)
        assert "DYNAMIC DISPATCH" not in prompt


# --- K-15: TestK15IngredientsTableInjection ---


class TestK15IngredientsTableInjection:
    _TABLE = "| Name | Description | Default |\n| task | What to fix | — |"

    def test_ingredients_section_present_when_provided(self) -> None:
        prompt = _build(ingredients_table=self._TABLE)
        assert "RECIPE INGREDIENTS" in prompt
        assert self._TABLE in prompt

    def test_ingredients_section_absent_when_none(self) -> None:
        prompt = _build(ingredients_table=None)
        assert "RECIPE INGREDIENTS" not in prompt

    def test_ask_user_question_instruction_present(self) -> None:
        prompt = _build(ingredients_table=self._TABLE)
        assert "AskUserQuestion" in prompt

    def test_ingredients_section_between_overview_and_manifest(self) -> None:
        prompt = _build(ingredients_table=self._TABLE)
        overview_pos = prompt.index("CAMPAIGN OVERVIEW")
        ingredients_pos = prompt.index("RECIPE INGREDIENTS")
        manifest_pos = prompt.index("DISPATCH MANIFEST")
        assert overview_pos < ingredients_pos < manifest_pos
