"""Tests for the cli/_prompts.py module."""

from __future__ import annotations


# PR1
def test_prompts_module_exists():
    pass  # ImportError if missing


# PR2
def test_build_orchestrator_prompt_importable_from_prompts():
    from autoskillit.cli._prompts import _build_orchestrator_prompt  # noqa: F401


# PR3
def test_build_orchestrator_prompt_contains_recipe_name():
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("my-recipe")
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


def test_orchestrator_prompt_delegates_ingredient_collection_to_load_recipe():
    """Orchestrator prompt must instruct Claude to call load_recipe for recipe content."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "load_recipe" in prompt, "Prompt must instruct Claude to call load_recipe"
    assert "collect ingredients" in prompt.lower(), (
        "Prompt must mention ingredient collection after load_recipe"
    )


def test_orchestrator_prompt_documents_confirm_action():
    """The orchestrator system prompt must explain how to handle action:confirm steps."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "action: confirm" in prompt or 'action: "confirm"' in prompt
    assert "AskUserQuestion" in prompt


def test_build_orchestrator_prompt_accepts_name_not_yaml():
    """_build_orchestrator_prompt takes a recipe name string, not raw YAML."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "my-recipe" in prompt
    assert "load_recipe" in prompt
    # Recipe YAML markers must not appear
    assert "--- RECIPE ---" not in prompt
    assert "--- END RECIPE ---" not in prompt


def test_orchestrator_prompt_instructs_load_recipe_first():
    """Prompt must instruct Claude to call load_recipe as its first action after open_kitchen."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "load_recipe" in prompt
    # load_recipe instruction must come before ingredient collection
    lr_idx = prompt.index("load_recipe")
    assert "collect" in prompt[lr_idx:].lower() or "ingredient" in prompt[lr_idx:].lower()


def test_orchestrator_prompt_contains_greeting_pool():
    """Orchestrator prompt includes food-service greetings with the recipe name."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "Good Burger" in prompt
    assert "Today's special" in prompt
    assert "my-recipe" in prompt


def test_orchestrator_prompt_no_diagram():
    """Orchestrator prompt must not contain diagram content."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "### Graph" not in prompt
    assert "### Inputs" not in prompt


# T2-C (updated for single-parameter signature)
def test_build_orchestrator_prompt_single_param():
    """Calling with a single recipe name returns a valid prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("implementation")
    assert isinstance(result, str)
    assert len(result) > 0
    assert "ROUTING RULES" in result
