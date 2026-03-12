"""Tests for the cli/_prompts.py module."""

from __future__ import annotations


# PR1
def test_prompts_module_exists():
    pass  # ImportError if missing


# PR2
def test_build_orchestrator_prompt_importable_from_prompts():
    from autoskillit.cli._prompts import _build_orchestrator_prompt  # noqa: F401


# PR3
def test_build_orchestrator_prompt_contains_recipe_yaml():
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("name: my-recipe\n")
    assert "name: my-recipe" in result


# PR4
def test_build_orchestrator_prompt_not_in_app_module():
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")

    src = inspect.getsource(app_mod)
    assert "def _build_orchestrator_prompt(" not in src, (
        "_build_orchestrator_prompt must be in cli/_prompts.py, not cli/app.py"
    )


def test_orchestrator_prompt_ingredient_collection_is_conversational():
    """Orchestrator prompt must use conversational ingredient collection, not per-field prompting."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("<dummy yaml>")
    # New conversational behavior must be present
    assert "infer" in prompt.lower(), (
        "Orchestrator prompt must instruct Claude to infer ingredient values"
    )
    assert "free-form" in prompt.lower() or "open-ended" in prompt.lower(), (
        "Orchestrator prompt must describe a free-form or open-ended question for ingredient collection"
    )
    # Old mechanical per-field instruction must be gone from the input-collection step
    # (AskUserQuestion may still appear in the confirm-step section — that's expected)
    lines = prompt.splitlines()
    mechanical_line = next(
        (l for l in lines if "Prompt for input values using AskUserQuestion" in l), None
    )
    assert mechanical_line is None, (
        "Orchestrator prompt must not contain 'Prompt for input values using AskUserQuestion' "
        "as a standalone instruction. Use conversational collection instead."
    )


def test_orchestrator_prompt_documents_confirm_action():
    """The orchestrator system prompt must explain how to handle action:confirm steps."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("<dummy yaml>")
    assert "action: confirm" in prompt or 'action: "confirm"' in prompt
    assert "AskUserQuestion" in prompt


# T2-A
def test_build_orchestrator_prompt_includes_diagram():
    """Prompt includes diagram content and FIRST ACTION instruction when diagram supplied."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("name: my-recipe\n", diagram="## Flow\n```\nA → B\n```")
    assert "## Flow" in result
    assert "A → B" in result
    assert "FIRST ACTION" in result


# T2-B
def test_build_orchestrator_prompt_diagram_none_unchanged():
    """Passing diagram=None preserves existing behavior (recipe YAML present, ROUTING RULES)."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("name: my-recipe\n", diagram=None)
    assert "name: my-recipe" in result
    assert "ROUTING RULES" in result


# T2-C
def test_build_orchestrator_prompt_positional_compat():
    """Calling without diagram kwarg (positional compat) still returns a valid prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("name: my-recipe\n")
    assert isinstance(result, str)
    assert len(result) > 0
