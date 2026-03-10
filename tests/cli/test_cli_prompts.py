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


def test_orchestrator_prompt_documents_confirm_action():
    """The orchestrator system prompt must explain how to handle action:confirm steps."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("<dummy yaml>")
    assert "action: confirm" in prompt or 'action: "confirm"' in prompt
    assert "AskUserQuestion" in prompt


def test_subrecipe_prompt_embeds_yaml():
    from autoskillit.cli._prompts import build_subrecipe_prompt

    yaml = "name: test\n"
    result = build_subrecipe_prompt(yaml, "{}")
    assert yaml in result
    assert "--- RECIPE ---" in result


def test_subrecipe_prompt_shows_ingredients():
    from autoskillit.cli._prompts import build_subrecipe_prompt

    p = build_subrecipe_prompt("n: t", '{"task": "fix bug"}')
    assert "task" in p and "fix bug" in p


def test_subrecipe_prompt_no_ingredient_collection_instruction():
    from autoskillit.cli._prompts import build_subrecipe_prompt

    assert "Prompt for input values" not in build_subrecipe_prompt("n: t", "{}")


def test_subrecipe_prompt_has_routing_rules():
    from autoskillit.cli._prompts import build_subrecipe_prompt

    p = build_subrecipe_prompt("n: t", "{}")
    assert "ROUTING RULES" in p and "on_failure" in p


def test_subrecipe_prompt_handles_invalid_json_gracefully():
    from autoskillit.cli._prompts import build_subrecipe_prompt

    # Must not raise
    assert "--- RECIPE ---" in build_subrecipe_prompt("n: t", "not-json")
