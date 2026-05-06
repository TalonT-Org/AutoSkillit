"""Shared helpers for fleet CLI tests."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from autoskillit.fleet import CampaignState


class DispatchDescriptor(NamedTuple):
    name: str
    status: str
    session_id: str


def _stub_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub all fleet_run guard conditions to pass."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")


def _stub_campaign_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> MagicMock:
    """Stub find_campaign_by_name, load_recipe, validate_recipe, and pre-launch steps."""
    campaign_path = tmp_path / f"{name}.yaml"
    campaign_path.write_text("")

    recipe_info = MagicMock()
    recipe_info.name = name
    recipe_info.path = campaign_path

    recipe = MagicMock()
    recipe.name = name
    recipe.dispatches = []
    recipe.continue_on_failure = False
    recipe.description = f"Test campaign {name}"

    monkeypatch.setattr("autoskillit.recipe.find_campaign_by_name", lambda *a, **kw: recipe_info)
    monkeypatch.setattr("autoskillit.recipe.load_recipe", lambda *a, **kw: recipe)
    monkeypatch.setattr("autoskillit.recipe.validate_recipe", lambda *a: [])
    monkeypatch.setattr("autoskillit.cli._preview.show_campaign_preview", lambda *a, **kw: None)
    monkeypatch.setattr("autoskillit.cli._prompts._get_ingredients_table", lambda *a, **kw: None)
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    return recipe


def _capture_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace subprocess.run with a capturing stub. Returns captured dict."""
    captured: dict = {}

    def mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    return captured


def _setup_existing_campaign_state(tmp_path: Path, campaign_id: str, campaign_name: str) -> None:
    """Create a state.json for an existing campaign in tmp_path."""
    from autoskillit.fleet import DispatchRecord, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    dispatches = [DispatchRecord(name="dispatch-1")]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_with_tokens(
    tmp_path: Path,
    campaign_id: str,
    campaign_name: str,
    *,
    token_usage: dict | None = None,
) -> None:
    """Create a state.json with a succeeded dispatch and token usage."""
    from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    if token_usage is None:
        token_usage = {}
    now = time.time()
    dispatches = [
        DispatchRecord(
            name="dispatch-1",
            status=DispatchStatus.SUCCESS,
            token_usage=token_usage,
            dispatched_session_log_dir="/tmp/test-session-log",
            started_at=now - 60.0,
            ended_at=now,
        )
    ]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_with_status(
    tmp_path: Path, campaign_id: str, campaign_name: str, *, status: str
) -> None:
    """Create a state.json with a single dispatch at a given status."""
    from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    dispatches = [DispatchRecord(name="dispatch-1", status=DispatchStatus(status))]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_with_sessions(
    tmp_path: Path,
    campaign_id: str,
    campaign_name: str,
    *,
    dispatches: list[DispatchDescriptor],
) -> None:
    """Create a state.json with dispatches that have dispatched_session_id set."""
    from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    records = [
        DispatchRecord(
            name=d.name, status=DispatchStatus(d.status), dispatched_session_id=d.session_id
        )
        for d in dispatches
    ]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", records
    )


def _make_state(*, statuses: list[str]) -> CampaignState:
    """Build an in-memory CampaignState for unit-testing _compute_exit_code."""
    from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

    dispatches = [
        DispatchRecord(name=f"d{i}", status=DispatchStatus(s)) for i, s in enumerate(statuses)
    ]
    return CampaignState(
        schema_version=2,
        campaign_id="test-id",
        campaign_name="test",
        manifest_path="manifest.yaml",
        started_at=0.0,
        dispatches=dispatches,
    )


def _make_state_with_tokens(*, input_total: int) -> CampaignState:
    """Build an in-memory CampaignState with known token totals."""
    from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

    dispatches = [
        DispatchRecord(
            name="dispatch-1",
            status=DispatchStatus.SUCCESS,
            token_usage={"input_tokens": input_total},
        )
    ]
    return CampaignState(
        schema_version=2,
        campaign_id="test-id",
        campaign_name="test",
        manifest_path="manifest.yaml",
        started_at=0.0,
        dispatches=dispatches,
    )


def _setup_campaign_recipes(tmp_path: Path, names: list[str]) -> None:
    """Create campaign recipe YAML files under .autoskillit/recipes/campaigns/."""
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    for name in names:
        recipe_yaml = (
            f"name: {name}\ndescription: Test campaign {name}\nkind: campaign\ndispatches: []\n"
        )
        (campaigns_dir / f"{name}.yaml").write_text(recipe_yaml)


def _mock_batch_delete(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub batch_delete and return the mock."""
    mock = MagicMock(return_value={"deleted": [], "delete_failures": [], "preserved": []})
    monkeypatch.setattr("autoskillit.workspace.batch_delete", mock)
    return mock


def _get_app() -> object:
    from autoskillit.cli.app import app

    return app


def _subcommand_names(app: object) -> set[str]:
    return set(app._commands.keys())  # type: ignore[attr-defined]


def _find_command(app: object, name: str) -> object:
    return app._commands.get(name)  # type: ignore[attr-defined]
