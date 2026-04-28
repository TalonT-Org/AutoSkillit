"""Tests for the cli/_prompts.py module."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._mcp_names import DIRECT_PREFIX, MARKETPLACE_PREFIX

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# PR1
def test_prompts_module_exists():
    pass  # ImportError if missing


# PR2
def test_build_orchestrator_prompt_importable_from_prompts():
    from autoskillit.cli._prompts import _build_orchestrator_prompt  # noqa: F401


# PR3
def test_build_orchestrator_prompt_contains_recipe_name():
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "my-recipe" in result


# PR4
def test_build_orchestrator_prompt_not_in_app_module():
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")

    src = inspect.getsource(app_mod)
    assert "def _build_orchestrator_prompt(" not in src, (
        "_build_orchestrator_prompt must be in cli/_prompts.py, not cli/app.py"
    )


def test_orchestrator_prompt_delegates_ingredient_collection_to_open_kitchen():
    """Orchestrator prompt must instruct Claude to call open_kitchen with recipe name."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "open_kitchen" in prompt, "Prompt must instruct Claude to call open_kitchen"
    assert "collect ingredient" in prompt.lower(), (
        "Prompt must mention ingredient collection after open_kitchen"
    )


def test_orchestrator_prompt_documents_confirm_action():
    """The orchestrator system prompt must explain how to handle action:confirm steps."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "action: confirm" in prompt or 'action: "confirm"' in prompt
    assert "AskUserQuestion" in prompt


def test_build_orchestrator_prompt_accepts_name_not_yaml():
    """_build_orchestrator_prompt takes a recipe name string, not raw YAML."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "my-recipe" in prompt
    assert "open_kitchen" in prompt
    # Recipe YAML markers must not appear
    assert "--- RECIPE ---" not in prompt
    assert "--- END RECIPE ---" not in prompt


def test_orchestrator_prompt_instructs_open_kitchen_with_recipe_first():
    """Prompt must instruct Claude to call open_kitchen(name) as its first action."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "open_kitchen" in prompt
    # open_kitchen instruction must come before ingredient collection
    ok_idx = prompt.index("open_kitchen")
    assert "collect" in prompt[ok_idx:].lower() or "ingredient" in prompt[ok_idx:].lower()


def test_orchestrator_prompt_open_kitchen_uses_keyword_form():
    """open_kitchen must use keyword argument form in the orchestrator prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    assert "open_kitchen(name=" in prompt, (
        "open_kitchen must use keyword argument form to avoid Claude guessing wrong param name"
    )


def test_orchestrator_prompt_does_not_contain_greeting_pool():
    """Greetings are delivered via positional arg, not embedded in system prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "Good Burger" not in prompt
    assert "Display ONE of these greetings" not in prompt
    assert "my-recipe" in prompt


def test_orchestrator_prompt_no_diagram():
    """Orchestrator prompt must not contain diagram content."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "### Graph" not in prompt
    assert "### Inputs" not in prompt


# T2-C (updated for single-parameter signature)
def test_build_orchestrator_prompt_single_param():
    """Calling with a single recipe name returns a valid prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    assert isinstance(result, str)
    assert len(result) > 0
    assert "ROUTING RULES" in result


def test_cook_greetings_all_render_recipe_name():
    """Every cook greeting must include the recipe name after formatting."""
    from autoskillit.cli._prompts import _COOK_GREETINGS

    for g in _COOK_GREETINGS:
        rendered = g.format(recipe_name="test-recipe")
        assert "test-recipe" in rendered, f"Greeting missing recipe name: {g!r}"
        assert "{" not in rendered, f"Unresolved placeholder in: {rendered!r}"


def test_open_kitchen_greetings_have_no_placeholders():
    """Open-kitchen greetings must not contain format placeholders."""
    from autoskillit.cli._prompts import _OPEN_KITCHEN_GREETINGS

    for g in _OPEN_KITCHEN_GREETINGS:
        assert "{" not in g, f"Placeholder in open-kitchen greeting: {g!r}"


def test_open_kitchen_prompt_does_not_embed_greetings():
    """Open-kitchen greetings are delivered via positional arg, not embedded."""
    from autoskillit.cli._prompts import _build_open_kitchen_prompt

    prompt = _build_open_kitchen_prompt(mcp_prefix=DIRECT_PREFIX)
    assert "Display ONE of these greetings" not in prompt


def test_show_cook_preview_prints_table(monkeypatch, tmp_path, capsys):
    """show_cook_preview prints ingredients table to stdout."""
    from autoskillit.cli._prompts import show_cook_preview
    from autoskillit.recipe.io import _parse_recipe

    monkeypatch.setattr(
        "autoskillit.config.resolve_ingredient_defaults",
        lambda _: {"source_dir": "https://github.com/test/repo", "base_branch": "main"},
    )
    recipe = _parse_recipe(
        {
            "name": "test",
            "steps": {
                "do": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo"},
                    "on_success": "done",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
            "ingredients": {"task": {"description": "What to do", "required": True}},
        }
    )
    show_cook_preview("test", recipe, tmp_path, tmp_path)
    captured = capsys.readouterr()
    assert "task" in captured.out
    assert "(required)" in captured.out


def test_show_cook_preview_no_diagram(monkeypatch, tmp_path, capsys):
    """show_cook_preview works when no diagram file exists."""
    from autoskillit.cli._prompts import show_cook_preview
    from autoskillit.recipe.io import _parse_recipe

    monkeypatch.setattr(
        "autoskillit.config.resolve_ingredient_defaults",
        lambda _: {},
    )
    recipe = _parse_recipe(
        {
            "name": "test",
            "steps": {
                "do": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo"},
                    "on_success": "done",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
            "ingredients": {"x": {"description": "A thing", "default": "val"}},
        }
    )
    show_cook_preview("nonexistent-recipe", recipe, tmp_path, tmp_path)
    captured = capsys.readouterr()
    # No diagram, but table should print
    assert "A thing" in captured.out
    # No diagram header
    assert "RECIPE" not in captured.out


def test_show_cook_preview_uses_resolved_base_branch_for_smoke_test(monkeypatch, capsys):
    """T8: show_cook_preview renders the config-resolved base_branch for smoke-test."""
    import autoskillit.recipe._api as api_mod
    from autoskillit.cli._prompts import show_cook_preview
    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import find_recipe_by_name, load_recipe

    project_dir = pkg_root().parent.parent
    monkeypatch.setattr(api_mod, "_LOAD_CACHE", {})
    monkeypatch.setattr(
        "autoskillit.config.resolve_ingredient_defaults",
        lambda _: {"base_branch": "integration"},
    )

    recipe_info = find_recipe_by_name("smoke-test", project_dir)
    assert recipe_info is not None, "smoke-test recipe must exist in project-local recipes"
    recipe = load_recipe(recipe_info.path)

    show_cook_preview("smoke-test", recipe, project_dir, project_dir)
    captured = capsys.readouterr()
    assert "integration" in captured.out
    # base_branch row must NOT show the YAML literal "main"
    base_branch_lines = [line for line in captured.out.splitlines() if "base_branch" in line]
    assert base_branch_lines, "base_branch must appear in the rendered table"
    assert all("main" not in line for line in base_branch_lines)


def test_orchestrator_prompt_contains_multi_issue_guidance():
    """System prompt must document the sequential vs parallel decision for multiple issues."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    assert prompt, "_build_orchestrator_prompt returned empty"
    # The sous-chef content is injected into the prompt; the rule must be present
    lower = prompt.lower()
    assert "sequential" in lower, "Prompt must mention sequential execution mode"
    assert "parallel" in lower, "Prompt must mention parallel execution mode"
    assert "multiple" in lower or "issues" in lower, (
        "Prompt must reference multiple issues context"
    )


def test_orchestrator_prompt_multi_issue_ask_only_two_options():
    """When mode unspecified, prompt must prescribe asking sequential-or-parallel only."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    # Must tell the orchestrator to ask the user — no other alternatives offered
    assert "sequentially (one at a time) or in parallel" in prompt.lower(), (
        "Prompt must instruct orchestrator to ask 'sequential or parallel?'"
    )


def test_orchestrator_prompt_gates_context_limit_on_retry_reason_resume():
    """The orchestrator prompt must gate on_context_limit routing on retry_reason=resume.

    This prevents empty_output, early_stop, and zero_writes retry reasons from
    being incorrectly routed to on_context_limit.
    """
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    # Must require retry_reason=resume to route to on_context_limit
    assert "retry_reason: resume" in prompt, (
        "Prompt must gate on_context_limit routing on retry_reason: resume"
    )
    # Routing is 2-dimensional: subtype discriminates stale from context_exhaustion
    assert "subtype" in prompt, "Prompt must reference subtype as a routing discriminant"
    assert "stale" in prompt, "Prompt must mention stale subtype to give it its own routing branch"


def test_orchestrator_prompt_empty_output_falls_to_on_failure():
    """The orchestrator prompt must explicitly route empty_output to on_failure."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    assert "empty_output" in prompt, "Prompt must mention empty_output retry_reason"
    # empty_output must be described as falling through to on_failure — not on_context_limit
    empty_output_idx = prompt.index("empty_output")
    segment = prompt[empty_output_idx : empty_output_idx + 400]
    assert "on_failure" in segment, (
        "Prompt must route empty_output to on_failure near the empty_output mention"
    )


def test_orchestrator_prompt_drain_race_routes_to_on_context_limit():
    """drain_race must be listed alongside resume as an on_context_limit trigger."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    assert "drain_race" in prompt, "Prompt must mention drain_race retry_reason"
    # drain_race must be associated with on_context_limit routing — not standalone
    drain_race_idx = prompt.index("drain_race")
    segment = prompt[drain_race_idx : drain_race_idx + 400]
    assert "on_context_limit" in segment, (
        "Prompt must route drain_race to on_context_limit near the drain_race mention"
    )


def test_orchestrator_prompt_path_contamination_falls_to_on_failure():
    """path_contamination must fall through to on_failure, not on_context_limit."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    assert "path_contamination" in prompt, "Prompt must mention path_contamination retry_reason"
    # path_contamination must be associated with on_failure routing
    pc_idx = prompt.index("path_contamination")
    segment = prompt[pc_idx : pc_idx + 500]
    assert "on_failure" in segment, (
        "Prompt must route path_contamination to on_failure near the path_contamination mention"
    )


def test_show_cook_preview_line_width_bounded_with_implementation_recipe(tmp_path, capsys):
    """show_cook_preview must not produce lines wider than 120 chars even for
    the real implementation.yaml with its 220-char run_mode description."""
    import re

    from autoskillit.cli._prompts import show_cook_preview
    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import find_recipe_by_name, load_recipe

    recipes_dir = pkg_root() / "recipes"
    recipe_info = find_recipe_by_name("implementation", recipes_dir)
    assert recipe_info is not None
    parsed = load_recipe(recipe_info.path)

    show_cook_preview("implementation", parsed, recipes_dir, tmp_path)
    captured = capsys.readouterr()
    for line in captured.out.splitlines():
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        assert len(plain) <= 120, f"Line too wide ({len(plain)} chars): {plain!r}"


def test_orchestrator_prompt_stale_retries_not_routed_to_context_limit():
    """Stale path (subtype=stale) must be retried, not routed to on_context_limit."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    # subtype=stale must appear as an explicit routing discriminant
    assert "subtype=stale" in prompt or "subtype: stale" in prompt
    # The prompt must explicitly prohibit routing stale to on_context_limit
    assert "subtype=stale" in prompt and "on_context_limit" in prompt
    never_idx = prompt.find("NEVER route")
    if never_idx != -1:
        never_window = prompt[never_idx : never_idx + 200]
        assert "subtype=stale" in never_window or "stale" in never_window.lower()
    else:
        # At minimum: stale discriminant must appear in a NOT/DO NOT context
        stale_idx = prompt.find("subtype=stale")
        window = prompt[stale_idx : stale_idx + 300]
        assert "retry" in window.lower()
        assert "do not" in window.lower() or "not" in window.lower()


def test_orchestrator_prompt_context_exhaustion_still_routes_to_context_limit():
    """Genuine context exhaustion must still route to on_context_limit."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
    # context_exhaustion subtype must be explicitly referenced — hard assertion, no fallback
    ctx_idx = prompt.find("context_exhaustion")
    assert ctx_idx != -1, (
        "prompt must reference 'context_exhaustion' subtype to route it to on_context_limit; "
        "dropping this token causes the routing guard to silently degrade"
    )
    window = prompt[ctx_idx : ctx_idx + 500]
    assert "on_context_limit" in window


# MCP prefix parametrisation tests
@pytest.mark.parametrize("mcp_prefix", [DIRECT_PREFIX, MARKETPLACE_PREFIX])
def test_orchestrator_prompt_uses_fully_qualified_tool_name(mcp_prefix: str) -> None:
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=mcp_prefix)
    assert f"{mcp_prefix}open_kitchen" in prompt


@pytest.mark.parametrize("mcp_prefix", [DIRECT_PREFIX, MARKETPLACE_PREFIX])
def test_open_kitchen_prompt_uses_fully_qualified_tool_name(mcp_prefix: str) -> None:
    from autoskillit.cli._prompts import _build_open_kitchen_prompt

    prompt = _build_open_kitchen_prompt(mcp_prefix=mcp_prefix)
    assert f"{mcp_prefix}open_kitchen" in prompt


def test_orchestrator_prompt_contains_quota_routing():
    """_build_orchestrator_prompt output includes QUOTA DENIAL ROUTING section."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("test-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "QUOTA DENIAL ROUTING" in prompt


def test_orchestrator_prompt_has_no_server_startup_recovery_block():
    """SERVER-STARTUP RECOVERY block must be removed — it misdiagnoses schema deferral
    as server startup latency."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_open_kitchen_prompt, _build_orchestrator_prompt

    for fn, name in [
        (_build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX), "orchestrator"),
        (_build_open_kitchen_prompt(mcp_prefix=DIRECT_PREFIX), "open_kitchen"),
    ]:
        assert "SERVER-STARTUP RECOVERY" not in fn, (
            f"{name} prompt must not contain SERVER-STARTUP RECOVERY"
        )
        assert "Wait 3 seconds" not in fn, f"{name} prompt must not contain 'Wait 3 seconds'"


def test_orchestrator_prompt_has_no_deferred_tool_recovery_conditional():
    """The conditional DEFERRED-TOOL RECOVERY block must not be present."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
    assert "schemas NOT loaded — calling directly will fail" not in prompt


def test_first_action_no_toolsearch_or_bash():
    """FIRST ACTION must not reference ToolSearch or Bash."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my_recipe", mcp_prefix=DIRECT_PREFIX)
    start = prompt.index("FIRST ACTION")
    end = prompt.index("During pipeline execution", start)
    first_action = prompt[start:end]
    assert "ToolSearch" not in first_action
    assert "Bash" not in first_action
    assert "sleep" not in first_action.lower()


def test_first_action_opens_with_open_kitchen():
    """FIRST ACTION step 1 must call open_kitchen directly — no preamble step."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my_recipe", mcp_prefix=DIRECT_PREFIX)
    start = prompt.index("FIRST ACTION")
    end = prompt.index("During pipeline execution", start)
    first_action = prompt[start:end]
    assert "open_kitchen" in first_action
    assert "\n0." not in first_action, "Step 0 must not exist — open_kitchen is step 1"
    assert "\n1." in first_action


def test_open_kitchen_prompt_no_toolsearch_or_bash():
    """open_kitchen call instruction must not reference ToolSearch or Bash."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_open_kitchen_prompt

    prompt = _build_open_kitchen_prompt(mcp_prefix=DIRECT_PREFIX)
    # Scope to the call instruction before the discipline block
    discipline_idx = prompt.index("IMPORTANT")
    preamble = prompt[:discipline_idx]
    assert "ToolSearch" not in preamble
    assert "Bash" not in preamble
    assert "sleep" not in preamble.lower()


def test_open_kitchen_prompt_calls_open_kitchen_directly():
    """_build_open_kitchen_prompt must instruct a direct open_kitchen call."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_open_kitchen_prompt

    prompt = _build_open_kitchen_prompt(mcp_prefix=DIRECT_PREFIX)
    assert "open_kitchen" in prompt


def test_orchestrator_prompt_contains_anti_skip_rule():
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
    assert "STEP EXECUTION IS NOT DISCRETIONARY" in prompt
    assert "NEVER skip a step because" in prompt


def test_open_kitchen_prompt_contains_anti_skip_rule():
    from autoskillit.cli._prompts import _build_open_kitchen_prompt

    prompt = _build_open_kitchen_prompt(mcp_prefix=DIRECT_PREFIX)
    assert "STEP EXECUTION IS NOT DISCRETIONARY" in prompt
    assert "NEVER skip a step because" in prompt


def test_orchestrator_prompt_closes_optional_semantics():
    """OPTIONAL STEP SEMANTICS must state that skip_when_false=false is the ONLY skip reason."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("test", mcp_prefix=DIRECT_PREFIX)
    idx = prompt.index("OPTIONAL STEP SEMANTICS")
    section = prompt[idx : idx + 500]
    assert "ONLY" in section


# ING-1
def test_build_orchestrator_prompt_injects_ingredients_table_when_provided():
    """When ingredients_table is supplied, it appears verbatim in the prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    table = "| Name | Description | Default |\n|------|-------------|---------|"
    prompt = _build_orchestrator_prompt(
        "my-recipe", mcp_prefix=DIRECT_PREFIX, ingredients_table=table
    )
    assert table in prompt, "ingredients_table content must appear verbatim in the prompt"


# ING-2
def test_build_orchestrator_prompt_omits_ingredients_section_when_none():
    """When ingredients_table is None (default), no RECIPE INGREDIENTS section is injected."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "RECIPE INGREDIENTS" not in prompt, (
        "No RECIPE INGREDIENTS section when ingredients_table is None"
    )


# ING-3
def test_build_orchestrator_prompt_first_action_mentions_tool_activation():
    """FIRST ACTION section must clarify open_kitchen is required for tool activation."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    first_action_start = prompt.index("FIRST ACTION")
    first_action_end = prompt.index("During pipeline execution", first_action_start)
    section = prompt[first_action_start:first_action_end]
    assert "tool" in section.lower() and (
        "activat" in section.lower() or "enable" in section.lower()
    ), "FIRST ACTION must clarify open_kitchen is required for tool activation/enabling"


# ING-4
def test_build_orchestrator_prompt_ingredients_section_before_first_action():
    """RECIPE INGREDIENTS section must appear before FIRST ACTION so LLM sees names first."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    table = "| task * | What to do | (required) |"
    prompt = _build_orchestrator_prompt("impl", mcp_prefix=DIRECT_PREFIX, ingredients_table=table)
    ing_idx = prompt.index("RECIPE INGREDIENTS")
    first_action_idx = prompt.index("FIRST ACTION")
    assert ing_idx < first_action_idx, (
        "RECIPE INGREDIENTS section must appear before FIRST ACTION in the prompt"
    )


def test_orchestrator_prompt_contains_skill_command_format_guidance():
    """Orchestrator prompt must instruct the LLM that skill_command is a literal template."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "SKILL_COMMAND FORMATTING" in prompt, (
        "Orchestrator prompt must contain a SKILL_COMMAND FORMATTING section. "
        "This prevents the LLM from adding markdown headers to skill_command values."
    )


def test_campaign_prompt_tool_claim_has_after_startup_qualifier():
    """Fleet campaign prompt must qualify the 6-tool claim as applying after startup only."""
    from unittest.mock import MagicMock

    from autoskillit.cli._prompts import _build_fleet_campaign_prompt

    recipe = MagicMock()
    recipe.name = "test-campaign"
    recipe.description = "Test"
    recipe.dispatches = [MagicMock()]
    recipe.continue_on_failure = False

    prompt = _build_fleet_campaign_prompt(
        campaign_recipe=recipe,
        manifest_yaml="dispatches: []",
        completed_dispatches="",
        mcp_prefix="mcp__autoskillit__",
        campaign_id="abc-123",
    )

    assert "Only these 6 tools are available in this session" not in prompt, (
        "The absolute 'only 6 tools available' claim is factually false during startup "
        "— must be qualified as 'after startup' or 'for campaign operations'"
    )
    lower = prompt.lower()
    assert "after startup" in lower or "startup" in lower or "operational" in lower, (
        "Campaign prompt must qualify the tool list as post-startup / operational tools"
    )


def test_orchestrator_prompt_documents_stop_action():
    """The orchestrator system prompt must explain how to handle action:stop steps."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert 'action: "stop"' in prompt or "action: stop" in prompt
    assert "TERMINATE" in prompt.upper() or "terminate" in prompt
    assert "message" in prompt


def test_orchestrator_prompt_documents_route_action():
    """The orchestrator system prompt must explain how to handle action:route steps."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert 'action: "route"' in prompt or "action: route" in prompt
    assert "on_result" in prompt
    assert "Do NOT call any MCP tool" in prompt or "no tool call" in prompt.lower()


def test_orchestrator_prompt_contains_hook_denial_compliance():
    """The orchestrator prompt must teach the model that ALL hook denials are mandatory."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert "HOOK DENIAL" in prompt.upper()
    assert "MANDATORY" in prompt.upper() or "mandatory" in prompt
    assert "permissionDecision" in prompt or "deny" in prompt.lower()


@pytest.mark.parametrize("action_type", ["stop", "confirm", "route"])
def test_orchestrator_prompt_documents_all_action_types(action_type):
    """Every recognized action type must have explicit behavioral semantics."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe", mcp_prefix=DIRECT_PREFIX)
    assert f'action: "{action_type}"' in prompt or f"action: {action_type}" in prompt


def test_campaign_prompt_tool_list_still_enumerates_six_tools():
    """The 6 operational tools must still be listed in the campaign prompt after the fix."""
    from unittest.mock import MagicMock

    from autoskillit.cli._prompts import _build_fleet_campaign_prompt

    recipe = MagicMock()
    recipe.name = "test-campaign"
    recipe.description = "Test"
    recipe.dispatches = [MagicMock()]
    recipe.continue_on_failure = False

    prompt = _build_fleet_campaign_prompt(
        campaign_recipe=recipe,
        manifest_yaml="dispatches: []",
        completed_dispatches="",
        mcp_prefix="mcp__autoskillit__",
        campaign_id="abc-123",
    )
    assert "dispatch_food_truck" in prompt
    assert "batch_cleanup_clones" in prompt
    assert "get_pipeline_report" in prompt
    assert "get_token_summary" in prompt
    assert "get_timing_summary" in prompt
    assert "get_quota_events" in prompt


def test_campaign_prompt_includes_gate_dispatch_handling_section():
    """Campaign prompt includes GATE DISPATCH HANDLING section when a gate dispatch is present."""
    from autoskillit.cli._prompts import _build_fleet_campaign_prompt
    from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind

    dispatch = CampaignDispatch(name="gate-check", gate="confirm", message="Proceed?")
    recipe = Recipe(
        name="test-campaign",
        description="Test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[dispatch],
        continue_on_failure=False,
    )

    prompt = _build_fleet_campaign_prompt(
        campaign_recipe=recipe,
        manifest_yaml="...",
        completed_dispatches="",
        mcp_prefix="mcp__autoskillit__",
        campaign_id="abc-123",
    )
    assert "GATE DISPATCH HANDLING" in prompt
    assert "AskUserQuestion" in prompt
    assert "dispatch_food_truck" in prompt


def test_show_cook_preview_no_mermaid_in_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T-PREVIEW-FORMAT: show_cook_preview must not leak Mermaid syntax to terminal."""
    from autoskillit.cli._prompts import show_cook_preview
    from autoskillit.recipe import find_recipe_by_name, load_recipe

    monkeypatch.setenv("NO_COLOR", "1")
    recipes_dir = find_recipe_by_name("implementation").parent
    parsed = load_recipe(recipes_dir / "implementation.yaml")
    show_cook_preview("implementation", parsed, recipes_dir, tmp_path)
    captured = capsys.readouterr()
    assert "flowchart TD" not in captured.out, "Mermaid syntax leaked to terminal"
    assert "S0[" not in captured.out, "Mermaid node syntax leaked to terminal"
    assert "-->" not in captured.out, "Mermaid arrow syntax leaked to terminal"
