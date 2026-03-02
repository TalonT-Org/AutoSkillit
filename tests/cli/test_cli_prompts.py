"""Tests for the cli/_prompts.py module."""

from __future__ import annotations


# PR1
def test_prompts_module_exists():
    import autoskillit.cli._prompts  # ImportError if missing


# PR2
def test_build_orchestrator_prompt_importable_from_prompts():
    from autoskillit.cli._prompts import _build_orchestrator_prompt  # noqa


# PR3
def test_build_orchestrator_prompt_contains_recipe_yaml():
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    result = _build_orchestrator_prompt("name: my-recipe\n")
    assert "name: my-recipe" in result


# PR4
def test_build_orchestrator_prompt_not_in_app_module():
    import inspect

    import autoskillit.cli.app as app_mod

    src = inspect.getsource(app_mod)
    assert "def _build_orchestrator_prompt(" not in src, (
        "_build_orchestrator_prompt must be in cli/_prompts.py, not cli/app.py"
    )
