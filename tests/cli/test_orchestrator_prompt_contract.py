"""Tests for orchestrator prompt contract: failure predicates and dispatch consistency."""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _get_prompt() -> str:
    """Return the orchestrator prompt for a demo recipe."""
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    return _build_orchestrator_prompt("demo", "mcp__autoskillit__")


class TestPhaseScopedRecipeStartupContract:
    """Startup recovery precedes received-result handling unconditionally."""

    @pytest.mark.parametrize(
        ("phase", "required_phrases"),
        [
            (
                "PRE-DISPATCH",
                (
                    "every failure",
                    "before classifying",
                    "bounded retry",
                    "do not explain",
                    "do not troubleshoot",
                    "do not output a free-text question",
                    "do not call AskUserQuestion",
                ),
            ),
            (
                "POST-RECEIPT",
                (
                    "CallToolResult",
                    "isError:true",
                    "isError:false",
                    "structured application result",
                ),
            ),
        ],
    )
    def test_phases_are_present_and_semantically_complete(
        self, phase: str, required_phrases: tuple[str, ...]
    ) -> None:
        prompt = _get_prompt()
        start = prompt.index("MCP STARTUP RECOVERY")
        end = prompt.index("During pipeline execution", start)
        section = prompt[start:end]
        phase_start = section.index(phase)
        phase_end = (
            section.index("POST-RECEIPT", phase_start) if phase == "PRE-DISPATCH" else len(section)
        )
        phase_text = section[phase_start:phase_end]
        assert all(phrase.lower() in phase_text.lower() for phrase in required_phrases)

    def test_recovery_precedes_generic_success_false_rules(self) -> None:
        prompt = _get_prompt()
        start = prompt.index("MCP STARTUP RECOVERY")
        end = prompt.index("During pipeline execution", start)
        section = prompt[start:end]
        assert section.index("PRE-DISPATCH") < section.index("POST-RECEIPT")
        assert section.index("recovery manifest") < section.index(
            "nonrecoverable application error"
        )
        assert "complete_recipe_initialization(initialization_id)" in section
        assert "before the first execution or mutation tool" in section

    def test_old_conflicting_termination_blocks_are_removed(self) -> None:
        prompt = _get_prompt()
        assert "FAILURE PREDICATE — open_kitchen" not in prompt
        assert "FAILURE PREDICATE — DEGRADED TOOL RESPONSE" not in prompt


class TestStep0ToolPredicateCoverage:
    """Every tool referenced in STEP 0 must have a failure predicate or shared rule."""

    def test_every_step0_tool_has_failure_predicate_or_shared_rule(self):
        """Parse STEP 0 section, extract tool names, assert each has a predicate."""
        prompt = _get_prompt()

        # Extract tool names from STEP 0 (tools appear as {mcp_prefix}<tool> or explicit names)
        step0_match = re.search(
            r"FIRST ACTION.*?(?=ROUTING RULES|FAILURE PREDICATES|During pipeline)",
            prompt,
            re.DOTALL,
        )
        assert step0_match is not None, "STEP 0 / FIRST ACTION section not found"
        step0_text = step0_match.group()

        # Find tool names: mcp__autoskillit__<tool>(<args>)
        tool_names = set(re.findall(r"mcp__autoskillit__(\w+)\(", step0_text))
        assert len(tool_names) > 0, "No tool names found in STEP 0"

        # Each tool must appear in a FAILURE PREDICATE section
        for tool in tool_names:
            assert (
                f"FAILURE PREDICATE — {tool}" in prompt
                or f"- {tool}:" in prompt
                or (tool == "open_kitchen" and "MCP STARTUP RECOVERY" in prompt)
            ), f"Tool '{tool}' in STEP 0 has no failure predicate or shared rule"


class TestFirstActionAskUserQuestionProhibition:
    """FIRST ACTION section must explicitly prohibit AskUserQuestion before open_kitchen."""

    def test_first_action_prohibits_ask_user_question_before_open_kitchen(self):
        """The FIRST ACTION section must contain an explicit prohibition on
        AskUserQuestion before open_kitchen."""
        prompt = _get_prompt()
        first_action_start = prompt.index("FIRST ACTION")
        # Find the end of the FIRST ACTION section (next major section)
        first_action_end = prompt.index("During pipeline execution", first_action_start)
        first_action_section = prompt[first_action_start:first_action_end]
        assert "DO NOT call AskUserQuestion" in first_action_section

    def test_first_action_instructs_retry_on_mcp_unavailable(self):
        """FIRST ACTION embeds the exact canonical startup-recovery contract."""
        from autoskillit.cli.prompts import (
            _MCP_RETRY_INSTRUCTION,
            _build_orchestrator_prompt,
        )

        prompt = _build_orchestrator_prompt("demo", "mcp__autoskillit__")
        fa_start = prompt.find("FIRST ACTION")
        assert fa_start != -1, "'FIRST ACTION' section not found in prompt"
        fa_end = prompt.find("During pipeline execution", fa_start)
        assert fa_end != -1, "'During pipeline execution' section not found after FIRST ACTION"
        first_action = prompt[fa_start:fa_end]

        normalized = "\n".join(line.removeprefix("   ") for line in first_action.splitlines())
        assert _MCP_RETRY_INSTRUCTION in normalized
        assert "Bash" not in first_action, "Retry instruction must not reference Bash"
        assert "ToolSearch" not in first_action, "Retry instruction must not reference ToolSearch"
        assert "sleep" not in first_action.lower(), "Retry instruction must not use sleep"


class TestOpenKitchenStartupPolicyEmbedding:
    """open-kitchen prompts embed the exact canonical policy."""

    def test_open_kitchen_prompt_embeds_canonical_policy(self):
        from autoskillit.cli.prompts import (
            _MCP_RETRY_INSTRUCTION,
            _build_open_kitchen_prompt,
        )

        ok_prompt = _build_open_kitchen_prompt("mcp__autoskillit__")
        first_section_end = ok_prompt.find("IMPORTANT \u2014 Orchestrator Discipline:")
        assert first_section_end != -1, (
            "'IMPORTANT \u2014 Orchestrator Discipline:' section not found in ok_prompt"
        )
        first_section = ok_prompt[:first_section_end]

        assert _MCP_RETRY_INSTRUCTION in first_section


class TestFirstActionDirectOpenKitchen:
    """FIRST ACTION must call open_kitchen directly — no ToolSearch or Bash preamble."""

    def test_first_action_no_step0(self):
        """FIRST ACTION must not contain a step 0."""
        prompt = _get_prompt()
        fa_start = prompt.index("FIRST ACTION")
        fa_end = prompt.index("During pipeline execution", fa_start)
        first_action = prompt[fa_start:fa_end]
        assert "\n0." not in first_action

    def test_first_action_no_toolsearch(self):
        """FIRST ACTION must not reference ToolSearch."""
        prompt = _get_prompt()
        fa_start = prompt.index("FIRST ACTION")
        fa_end = prompt.index("During pipeline execution", fa_start)
        first_action = prompt[fa_start:fa_end]
        assert "ToolSearch" not in first_action

    def test_first_action_no_bash_sleep(self):
        """FIRST ACTION must not reference Bash or sleep."""
        prompt = _get_prompt()
        fa_start = prompt.index("FIRST ACTION")
        fa_end = prompt.index("During pipeline execution", fa_start)
        first_action = prompt[fa_start:fa_end]
        assert "Bash" not in first_action
        assert "sleep" not in first_action.lower()


def test_orchestrator_prompt_prohibits_raw_file_reading():
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt(
        "implementation",
        mcp_prefix="mcp__autoskillit_",
        has_unguarded_filesystem_access=True,
    )
    assert "NEVER read recipe YAML files from the filesystem" in prompt
    assert "load_recipe" in prompt


def test_orchestrator_prompt_has_universal_raw_file_prohibition():
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix="mcp__autoskillit_")
    assert "NEVER read recipe YAML files from the filesystem" in prompt


def test_unguarded_filesystem_backend_supplement_injected():
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt(
        "implementation",
        mcp_prefix="mcp__autoskillit_",
        has_unguarded_filesystem_access=True,
    )
    assert "run_cmd" in prompt
    assert "BACKEND-SPECIFIC CONSTRAINTS" in prompt


def test_guarded_backend_no_filesystem_supplement():
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt(
        "implementation",
        mcp_prefix="mcp__autoskillit_",
        has_unguarded_filesystem_access=False,
    )
    assert "NEVER read recipe YAML files from the filesystem" in prompt
    assert "BACKEND-SPECIFIC CONSTRAINTS" not in prompt


@pytest.mark.parametrize(
    "func_name,module",
    [
        ("_build_orchestrator_prompt", "autoskillit.cli.prompts._prompts_orchestrator"),
        ("_build_open_kitchen_prompt", "autoskillit.cli.prompts._prompts_kitchen"),
        ("_build_fleet_dispatch_prompt", "autoskillit.cli.prompts._prompts_kitchen"),
        ("_build_food_truck_prompt", "autoskillit.fleet._prompts"),
        ("_build_fleet_campaign_prompt", "autoskillit.cli.prompts._prompts_campaign"),
    ],
)
def test_prompt_builders_accept_filesystem_access_param(func_name: str, module: str):
    import importlib
    import inspect

    mod = importlib.import_module(module)
    func = getattr(mod, func_name)
    sig = inspect.signature(func)
    assert "has_unguarded_filesystem_access" in sig.parameters


def test_cook_prompt_skip_guard_parity_with_fleet():
    """The cook prompt must handle skip_when_false resolution at least as correctly as the
    fleet prompt — which passes overrides to open_kitchen."""
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    cook_prompt = _build_orchestrator_prompt("remediation", mcp_prefix="mcp__autoskillit__")
    cook_has_resolution = (
        "overrides=" in cook_prompt
        or "deferred" in cook_prompt.lower()
        or "resolve" in cook_prompt.lower()
    )
    assert cook_has_resolution, (
        "Fleet prompt passes overrides to open_kitchen but cook prompt has no "
        "skip-guard resolution mechanism. Steps with skip_when_false defaults of 'false' "
        "are irreversibly pruned before the user is asked for their preferences."
    )


def test_food_truck_prompt_passes_overrides_to_open_kitchen():
    """The L2 food truck prompt must pass ingredient overrides to open_kitchen,
    ensuring skip_when_false guards are resolved with actual values."""
    from autoskillit.fleet._prompts import _build_food_truck_prompt

    prompt = _build_food_truck_prompt(
        recipe="remediation",
        task="test-task",
        ingredients={"review_approach": "true"},
        mcp_prefix="mcp__autoskillit__",
        dispatch_id="test-dispatch-id",
        campaign_id="test-campaign-id",
        l3_timeout_sec=600,
    )
    assert "overrides=" in prompt, (
        "Food truck prompt does not pass overrides to open_kitchen. "
        "Steps guarded by skip_when_false may be irreversibly pruned using defaults."
    )
