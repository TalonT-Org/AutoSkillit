"""Tests for the shared pre-launch preview module (_preview.py)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from autoskillit.cli._preview import (
    _render_pre_launch_preview,
    show_campaign_preview,
    show_cook_preview,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _make_recipe(monkeypatch: pytest.MonkeyPatch, *, has_ingredients: bool = True) -> object:
    from autoskillit.recipe.io import _parse_recipe

    monkeypatch.setattr(
        "autoskillit.config.resolve_ingredient_defaults",
        lambda _: {"source_dir": "https://github.com/test/repo"},
    )
    ingredients = (
        {"task": {"description": "What to build", "required": True}} if has_ingredients else {}
    )
    return _parse_recipe(
        {
            "name": "preview-test",
            "steps": {
                "do": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo hi"},
                    "on_success": "done",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
            "ingredients": ingredients,
        }
    )


class TestRenderPreLaunchPreview:
    def test_renders_ingredients_when_present(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recipe = _make_recipe(monkeypatch)
        _render_pre_launch_preview("preview-test", recipe, tmp_path, tmp_path)
        out = capsys.readouterr().out
        assert "task" in out
        assert "(required)" in out

    def test_skips_diagram_when_none(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recipe = _make_recipe(monkeypatch)
        _render_pre_launch_preview("nonexistent-recipe", recipe, tmp_path, tmp_path)
        out = capsys.readouterr().out
        assert "task" in out
        assert "RECIPE" not in out

    def test_skips_ingredients_when_empty(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recipe = _make_recipe(monkeypatch, has_ingredients=False)
        _render_pre_launch_preview("nonexistent-recipe", recipe, tmp_path, tmp_path)
        out = capsys.readouterr().out
        assert out.strip() == ""


class TestShowCookPreview:
    def test_signature_matches_expected(self) -> None:
        sig = inspect.signature(show_cook_preview)
        params = list(sig.parameters)
        assert params == ["recipe_name", "parsed_recipe", "recipes_dir", "project_dir"]

    def test_produces_terminal_output(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recipe = _make_recipe(monkeypatch)
        show_cook_preview("preview-test", recipe, tmp_path, tmp_path)
        out = capsys.readouterr().out
        assert "task" in out
        assert "(required)" in out


class TestShowCampaignPreview:
    def test_renders_ingredients_for_campaign(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recipe = _make_recipe(monkeypatch)
        show_campaign_preview("preview-test", recipe, tmp_path, tmp_path)
        out = capsys.readouterr().out
        assert "task" in out

    def test_skips_diagram_gracefully(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        recipe = _make_recipe(monkeypatch)
        show_campaign_preview("no-such-campaign-recipe", recipe, tmp_path, tmp_path)
        out = capsys.readouterr().out
        assert "task" in out
        assert "RECIPE" not in out
