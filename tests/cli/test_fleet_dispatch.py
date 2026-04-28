"""Tests: fleet CLI dispatch command."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cyclopts import App

from autoskillit.cli._fleet import fleet_dispatch as _fleet_dispatch
from tests.cli._fleet_helpers import (
    _capture_subprocess,
    _stub_guards,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


def _get_app() -> App:
    from autoskillit.cli.app import app

    return app


def _subcommand_names(app: App) -> set[str]:
    return set(app._commands.keys())  # type: ignore[attr-defined]


def _find_command(app: App, name: str) -> object:
    return app._commands.get(name)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# T1. fleet dispatch — CLAUDECODE guard
# ---------------------------------------------------------------------------


def test_fleet_dispatch_rejects_inside_claude_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when CLAUDECODE env is set."""
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T2. fleet dispatch — SESSION_TYPE=leaf guard
# ---------------------------------------------------------------------------


def test_fleet_dispatch_rejects_leaf_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when ambient SESSION_TYPE is leaf."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T3. fleet dispatch — claude not on PATH
# ---------------------------------------------------------------------------


def test_fleet_dispatch_exits_when_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when claude is not on PATH."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T_GUARD_1: fleet_dispatch exits 1 when fleet feature disabled
# ---------------------------------------------------------------------------


def test_fleet_dispatch_exits_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: (
            checked_features.append(name) or False
        ),
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_dispatch()
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


# ---------------------------------------------------------------------------
# T_GUARD_4: fleet_dispatch proceeds normally when fleet enabled
# ---------------------------------------------------------------------------


def test_fleet_dispatch_proceeds_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch passes the feature guard and proceeds to campaign resolution."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: True,
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    assert captured.get("cmd") is not None, "Expected fleet session subprocess to be invoked"
    assert captured["env"].get("AUTOSKILLIT_FLEET_MODE") == "dispatch"


# ---------------------------------------------------------------------------
# CLI registration tests
# ---------------------------------------------------------------------------


class TestFleetCLIRegistration:
    def test_fleet_subcommand_registered(self) -> None:
        app = _get_app()
        names = _subcommand_names(app)
        assert "fleet" in names

    def test_fleet_status_accepts_reap_flag(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        status_cmd = _find_command(fleet_app, "status")
        assert status_cmd is not None, "fleet status command not found"

    def test_fleet_status_accepts_dry_run_flag(self) -> None:
        import inspect

        from autoskillit.cli._fleet import fleet_status

        sig = inspect.signature(fleet_status)
        assert "dry_run" in sig.parameters
        assert "reap" in sig.parameters

    def test_fleet_dispatch_command_registered(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        assert "dispatch" in _subcommand_names(fleet_app)

    def test_fleet_campaign_command_registered(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        assert "campaign" in _subcommand_names(fleet_app)

    def test_fleet_run_command_not_registered(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        assert "run" not in _subcommand_names(fleet_app)


# ---------------------------------------------------------------------------
# T_ADHOC. Ad-hoc fleet dispatch mode (campaign_name=None)
# ---------------------------------------------------------------------------


def test_fleet_dispatch_sets_fleet_mode_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch() must launch an interactive session, not exit 1."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    assert captured["env"].get("AUTOSKILLIT_FLEET_MODE") == "dispatch"


def test_fleet_dispatch_writes_no_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_dispatch must not create a state.json under .autoskillit/temp/fleet/."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    fleet_dir = tmp_path / ".autoskillit" / "temp" / "fleet"
    assert not fleet_dir.exists() or not any(fleet_dir.rglob("state.json"))


def test_fleet_dispatch_no_campaign_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ad-hoc session must not set AUTOSKILLIT_CAMPAIGN_ID or AUTOSKILLIT_CAMPAIGN_STATE_PATH."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    env = captured["env"]
    assert "AUTOSKILLIT_CAMPAIGN_ID" not in env
    assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in env


def test_build_fleet_dispatch_prompt_no_open_kitchen() -> None:
    """Fleet dispatch prompt must NOT instruct calling open_kitchen."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "open_kitchen" not in prompt


def test_build_fleet_dispatch_prompt_references_dispatch_tool() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "dispatch_food_truck" in prompt


def test_build_fleet_dispatch_prompt_no_campaign_manifest() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "DISPATCH MANIFEST" not in prompt
    assert "CAMPAIGN OVERVIEW" not in prompt
    assert "CAMPAIGN DISCIPLINE" not in prompt


def test_build_fleet_dispatch_prompt_accepts_marketplace_prefix() -> None:
    from autoskillit.cli._mcp_names import MARKETPLACE_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(MARKETPLACE_PREFIX)
    assert MARKETPLACE_PREFIX + "open_kitchen" not in prompt
    assert MARKETPLACE_PREFIX + "dispatch_food_truck" in prompt


def test_build_fleet_dispatch_prompt_lists_all_10_tools() -> None:
    """Dispatch prompt must enumerate all 10 tools in the TOOL SURFACE section."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "TOOL SURFACE" in prompt, "Expected TOOL SURFACE section in dispatch prompt"
    tool_surface_start = prompt.index("TOOL SURFACE")
    next_section = prompt.find("\n##", tool_surface_start + 1)
    tool_surface = (
        prompt[tool_surface_start:next_section]
        if next_section != -1
        else prompt[tool_surface_start:]
    )
    for tool in (
        "dispatch_food_truck",
        "batch_cleanup_clones",
        "get_pipeline_report",
        "get_token_summary",
        "get_timing_summary",
        "get_quota_events",
        "list_recipes",
        "load_recipe",
        "fetch_github_issue",
        "get_issue_title",
    ):
        assert tool in tool_surface, (
            f"Expected tool {tool!r} in TOOL SURFACE section of dispatch prompt"
        )


def test_build_fleet_dispatch_prompt_includes_sous_chef_sections() -> None:
    """Dispatch prompt must include the 4 L2 sous-chef sections."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt
    from autoskillit.fleet import _build_l2_sous_chef_block

    sous_chef_block = _build_l2_sous_chef_block()
    assert sous_chef_block
    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "SOUS-CHEF DISCIPLINE" in prompt
    for section in (
        "CONTEXT LIMIT ROUTING",
        "STEP NAME IMMUTABILITY",
        "MERGE PHASE",
        "QUOTA WAIT PROTOCOL",
    ):
        assert section in prompt, f"Expected sous-chef section {section!r} in dispatch prompt"


def test_build_fleet_dispatch_prompt_has_recipe_discovery_guidance() -> None:
    """Dispatch prompt must guide recipe discovery flow."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "list_recipes" in prompt
    assert "load_recipe" in prompt


def test_build_fleet_dispatch_prompt_role_text() -> None:
    """Dispatch prompt must identify role as fleet dispatcher."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "fleet dispatcher" in prompt.lower()


def test_build_fleet_dispatch_prompt_has_cleanup_protocol() -> None:
    """Dispatch prompt must include batch_cleanup_clones exit instruction."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "batch_cleanup_clones" in prompt


def test_build_fleet_dispatch_prompt_no_sleep_toolsearch_preamble() -> None:
    """Dispatch prompt must NOT include sleep/ToolSearch boot sequence."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "sleep 2" not in prompt
