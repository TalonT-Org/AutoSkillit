"""Tests for --profile flag in cook command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import autoskillit.cli.session._cook as cook_module
from autoskillit.execution.commands import ClaudeInteractiveCmd

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _fake_build_cmd():
    captured = []

    def fake(**kwargs):
        captured.append(kwargs.get("env_extras", {}))
        return ClaudeInteractiveCmd(cmd=["claude"], env={})

    return fake, captured


@pytest.fixture()
def _mock_mgr():
    return MagicMock()


def _run_cook(profile, cfg, mock_mgr):
    fake_build, captured = _fake_build_cmd()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("builtins.input", return_value=""),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
        patch("autoskillit.execution.build_interactive_cmd", fake_build),
        patch("autoskillit.core.write_registry_entry"),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch(
            "autoskillit.cli.session._cook.is_feature_enabled",
            side_effect=lambda key, *a, **kw: key == "providers",
        ),
    ):
        cook_module.cook(profile=profile)
    return captured


def test_profile_valid_injects_provider_env_var(_mock_mgr):
    """AUTOSKILLIT_PROVIDER_PROFILE must be in env_extras when --profile is given."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {"minimax": {"ANTHROPIC_BASE_URL": "https://minimax.example"}}
    captured = _run_cook("minimax", cfg, _mock_mgr)
    env = captured[0]
    assert env.get("AUTOSKILLIT_PROVIDER_PROFILE") == "minimax"


def test_profile_valid_injects_profile_env_vars(_mock_mgr):
    """Profile's own env vars (API creds) must be injected into env_extras."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {
        "minimax": {"ANTHROPIC_BASE_URL": "https://mm.io", "ANTHROPIC_API_KEY": "sk-mm"}
    }
    captured = _run_cook("minimax", cfg, _mock_mgr)
    env = captured[0]
    assert env.get("ANTHROPIC_BASE_URL") == "https://mm.io"
    assert env.get("ANTHROPIC_API_KEY") == "sk-mm"


def test_profile_none_does_not_inject_provider_env(_mock_mgr):
    """When profile=None, AUTOSKILLIT_PROVIDER_PROFILE must NOT appear in env_extras."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {}
    captured = _run_cook(None, cfg, _mock_mgr)
    env = captured[0]
    assert "AUTOSKILLIT_PROVIDER_PROFILE" not in env


def test_profile_feature_disabled_exits(capsys, _mock_mgr):
    """SystemExit(1) with informative message when providers feature is not enabled."""
    cfg = MagicMock()
    cfg.experimental_enabled = False
    cfg.providers.profiles = {"minimax": {}}
    fake_build, _ = _fake_build_cmd()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=_mock_mgr),
        patch("autoskillit.execution.build_interactive_cmd", fake_build),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._cook.is_feature_enabled", return_value=False),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(profile="minimax")
    assert exc_info.value.code == 1
    assert "providers" in capsys.readouterr().err


def test_profile_unknown_exits(capsys, _mock_mgr):
    """SystemExit(1) with informative message listing known profiles for unknown name."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {"anthropic": {}, "openai": {}}
    fake_build, _ = _fake_build_cmd()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=_mock_mgr),
        patch("autoskillit.execution.build_interactive_cmd", fake_build),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._cook.is_feature_enabled", return_value=True),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(profile="minimax")
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "minimax" in err
    assert "anthropic" in err or "openai" in err
