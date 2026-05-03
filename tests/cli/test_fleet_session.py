"""Tests: _launch_fleet_session forwards ingredients_table to prompt builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_campaign_recipe(name: str = "test-campaign") -> MagicMock:
    recipe = MagicMock()
    recipe.name = name
    recipe.dispatches = []
    recipe.continue_on_failure = False
    recipe.description = f"Test {name}"
    return recipe


class TestLaunchFleetSessionIngredientsTable:
    def _call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        ingredients_table: str | None = None,
    ) -> dict:
        captured: dict = {}

        def _fake_build(
            campaign_recipe: object,
            manifest_yaml: str,
            completed_dispatches: str,
            mcp_prefix: str,
            campaign_id: str,
            **kwargs: object,
        ) -> str:
            captured["ingredients_table"] = kwargs.get("ingredients_table")
            return "fake-prompt"

        monkeypatch.setattr("autoskillit.cli._prompts._build_fleet_campaign_prompt", _fake_build)
        monkeypatch.setattr(
            "autoskillit.cli._session_launch._run_interactive_session",
            lambda *a, **kw: None,
        )
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id").mkdir(parents=True)

        state_path = tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id" / "state.json"
        state_path.write_text("{}")

        from autoskillit.cli._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
            ingredients_table=ingredients_table,
        )
        return captured

    def test_ingredients_table_forwarded_to_prompt_builder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        table = "| Name | Desc |\n| task | do it |"
        captured = self._call(monkeypatch, tmp_path, ingredients_table=table)
        assert captured["ingredients_table"] == table

    def test_ingredients_table_none_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._call(monkeypatch, tmp_path)
        assert captured["ingredients_table"] is None


class TestLaunchFleetSessionContinueOnFailureEnv:
    def _capture_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, continue_on_failure: bool
    ) -> dict:
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured["extra_env"] = kwargs.get("extra_env", {})
            return None

        monkeypatch.setattr("autoskillit.cli._session_launch._run_interactive_session", _fake_run)
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )
        monkeypatch.chdir(tmp_path)

        state_path = tmp_path / "state.json"
        state_path.write_text("{}")

        recipe = _make_campaign_recipe()
        recipe.continue_on_failure = continue_on_failure

        from autoskillit.cli._fleet_session import _launch_fleet_session

        _launch_fleet_session(recipe, "test-id", state_path, None, fleet_mode="campaign")
        return captured

    def test_continue_on_failure_false_injects_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._capture_env(monkeypatch, tmp_path, continue_on_failure=False)
        assert captured["extra_env"]["AUTOSKILLIT_CONTINUE_ON_FAILURE"] == "false"

    def test_continue_on_failure_true_injects_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._capture_env(monkeypatch, tmp_path, continue_on_failure=True)
        assert captured["extra_env"]["AUTOSKILLIT_CONTINUE_ON_FAILURE"] == "true"
