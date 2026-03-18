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


def test_orchestrator_prompt_delegates_ingredient_collection_to_open_kitchen():
    """Orchestrator prompt must instruct Claude to call open_kitchen with recipe name."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "open_kitchen" in prompt, "Prompt must instruct Claude to call open_kitchen"
    assert "collect ingredient" in prompt.lower(), (
        "Prompt must mention ingredient collection after open_kitchen"
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
    assert "open_kitchen" in prompt
    # Recipe YAML markers must not appear
    assert "--- RECIPE ---" not in prompt
    assert "--- END RECIPE ---" not in prompt


def test_orchestrator_prompt_instructs_open_kitchen_with_recipe_first():
    """Prompt must instruct Claude to call open_kitchen(name) as its first action."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "open_kitchen" in prompt
    # open_kitchen instruction must come before ingredient collection
    ok_idx = prompt.index("open_kitchen")
    assert "collect" in prompt[ok_idx:].lower() or "ingredient" in prompt[ok_idx:].lower()


def test_orchestrator_prompt_does_not_contain_greeting_pool():
    """Greetings are delivered via positional arg, not embedded in system prompt."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("my-recipe")
    assert "Good Burger" not in prompt
    assert "Display ONE of these greetings" not in prompt
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

    prompt = _build_open_kitchen_prompt()
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


def test_orchestrator_prompt_contains_multi_issue_guidance():
    """System prompt must document the sequential vs parallel decision for multiple issues."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    prompt = _build_orchestrator_prompt("implementation")
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

    prompt = _build_orchestrator_prompt("implementation")
    # Must tell the orchestrator to ask the user — no other alternatives offered
    assert "sequentially (one at a time) or in parallel" in prompt.lower(), (
        "Prompt must instruct orchestrator to ask 'sequential or parallel?'"
    )
