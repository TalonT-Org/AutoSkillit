"""Tests for the cli/_prompts.py module."""

from __future__ import annotations

from unittest.mock import patch


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


# T5a
def test_build_orchestrator_prompt_includes_sous_chef(tmp_path):
    """The CLI prompt includes sous-chef global orchestration rules when SKILL.md exists."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    # Create a fake sous-chef SKILL.md
    sous_chef_dir = tmp_path / "skills" / "sous-chef"
    sous_chef_dir.mkdir(parents=True)
    skill_md = sous_chef_dir / "SKILL.md"
    skill_md.write_text("# Sous-Chef Rules\nAlways delegate investigation.")

    with patch("autoskillit.cli._prompts.pkg_root", return_value=tmp_path):
        result = _build_orchestrator_prompt("name: test\n")

    assert "Sous-Chef Rules" in result
    assert "Always delegate investigation." in result


# T5b
def test_build_orchestrator_prompt_no_souschef(tmp_path):
    """When SKILL.md is absent, prompt still builds without error."""
    from autoskillit.cli._prompts import _build_orchestrator_prompt

    # tmp_path has no sous-chef directory
    with patch("autoskillit.cli._prompts.pkg_root", return_value=tmp_path):
        result = _build_orchestrator_prompt("name: test\n")

    assert "name: test" in result  # Recipe content still present
    assert "RECIPE" in result  # Recipe delimiters still present
