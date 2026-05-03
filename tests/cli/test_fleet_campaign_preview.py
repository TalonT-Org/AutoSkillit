"""Tests: fleet_campaign shows preview + confirmation before launch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._fleet import fleet_campaign as _fleet_campaign
from tests.cli._fleet_helpers import (
    _stub_campaign_resolution,
    _stub_guards,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


def _stub_preview_layer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timed_prompt_return: str = "",
    ingredients_table: str | None = None,
) -> dict:
    """Stub preview/prompt/launch layer; return captured call tracker."""
    calls: dict = {"preview": [], "launch": [], "timed_prompt": []}

    def _fake_preview(*args: object, **kwargs: object) -> None:
        calls["preview"].append(args)

    def _fake_get_itable(*args: object, **kwargs: object) -> str | None:
        return ingredients_table

    def _fake_timed_prompt(*args: object, **kwargs: object) -> str:
        calls["timed_prompt"].append((args, kwargs))
        return timed_prompt_return

    def _fake_launch(*args: object, **kwargs: object) -> None:
        calls["launch"].append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr("autoskillit.cli._preview.show_campaign_preview", _fake_preview)
    monkeypatch.setattr("autoskillit.cli._prompts._get_ingredients_table", _fake_get_itable)
    monkeypatch.setattr("autoskillit.cli._timed_input.timed_prompt", _fake_timed_prompt)
    monkeypatch.setattr("autoskillit.cli._fleet._launch_fleet_session", _fake_launch)
    return calls


class TestFleetCampaignPreview:
    def test_ingredient_table_displayed_before_launch(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
        calls = _stub_preview_layer(monkeypatch, ingredients_table="| Name | Desc |\n| task | ... |")
        _fleet_campaign("test-campaign")
        assert len(calls["preview"]) == 1

    def test_confirmation_prompt_shown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
        calls = _stub_preview_layer(monkeypatch)
        _fleet_campaign("test-campaign")
        assert len(calls["timed_prompt"]) == 1
        args, kwargs = calls["timed_prompt"][0]
        assert "campaign" in (args[0] if args else kwargs.get("prompt", "")).lower()

    def test_user_declines_does_not_launch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
        calls = _stub_preview_layer(monkeypatch, timed_prompt_return="n")
        _fleet_campaign("test-campaign")
        assert len(calls["launch"]) == 0

    def test_ingredients_table_passed_to_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
        table = "| Name | Value |\n| task | build |"
        calls = _stub_preview_layer(monkeypatch, ingredients_table=table)
        _fleet_campaign("test-campaign")
        assert len(calls["launch"]) == 1
        assert calls["launch"][0]["kwargs"].get("ingredients_table") == table

    def test_resume_skips_preview_and_confirmation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from tests.cli._fleet_helpers import _setup_existing_campaign_state

        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        campaign_id = "abc123def456ab12"
        _setup_existing_campaign_state(tmp_path, campaign_id, "test-campaign")
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
        calls = _stub_preview_layer(monkeypatch)
        _fleet_campaign("test-campaign", resume_campaign=campaign_id)
        assert len(calls["preview"]) == 0
        assert len(calls["timed_prompt"]) == 0
        assert len(calls["launch"]) == 1

    def test_resume_still_passes_ingredients_table_to_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from tests.cli._fleet_helpers import _setup_existing_campaign_state

        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        campaign_id = "abc123def456ab12"
        _setup_existing_campaign_state(tmp_path, campaign_id, "test-campaign")
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
        table = "| Name | Value |\n| task | fix |"
        calls = _stub_preview_layer(monkeypatch, ingredients_table=table)
        _fleet_campaign("test-campaign", resume_campaign=campaign_id)
        assert len(calls["launch"]) == 1
        assert calls["launch"][0]["kwargs"].get("ingredients_table") == table
