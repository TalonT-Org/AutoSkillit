"""Tests for --profile flag in cook command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import autoskillit.cli.session._session_cook as cook_module
from autoskillit.config import AutomationConfig
from autoskillit.core import BackendConventions, CmdSpec, SkillContractError

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _make_mock_backend_class():
    captured = []

    class _MockBackend:
        name = "claude-code"
        conventions = BackendConventions()

        def binary_name(self) -> str:
            return "claude"

        def build_interactive_cmd(self, **kwargs):
            captured.append(kwargs.get("env_extras", {}))
            return CmdSpec(cmd=("claude",), env={})

        def ensure_pre_launch(self) -> list[str]:
            return []

    return _MockBackend, captured


@pytest.fixture()
def _mock_mgr():
    return MagicMock()


def _run_cook(profile, cfg, mock_mgr):
    mock_backend_cls, captured = _make_mock_backend_class()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("builtins.input", return_value=""),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
        # cook() derives project_dir via the shared git-toplevel helper; pin it so
        # the wholesale subprocess.run mock cannot stand in for the git probe.
        patch("autoskillit.cli.session._session_cook.resolve_project_dir", Path.cwd),
        patch("subprocess.run", return_value=MagicMock(returncode=0)),
        patch("autoskillit.core.write_registry_entry"),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch(
            "autoskillit.cli.session._session_cook.is_feature_enabled",
            side_effect=lambda key, *a, **kw: key == "providers",
        ),
        patch("autoskillit.cli.ui._timed_input.timed_prompt", return_value=""),
    ):
        cook_module.cook(profile=profile, backend=mock_backend_cls())
    return captured


def test_profile_valid_injects_provider_env_var(_mock_mgr):
    """AUTOSKILLIT_PROVIDER_PROFILE must be in env_extras when --profile is given."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {"minimax": {"ANTHROPIC_BASE_URL": "https://minimax.example"}}
    captured = _run_cook("minimax", cfg, _mock_mgr)
    assert len(captured) >= 1, "build_interactive_cmd was not called"
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
    assert len(captured) >= 1, "build_interactive_cmd was not called"
    env = captured[0]
    assert env.get("ANTHROPIC_BASE_URL") == "https://mm.io"
    assert env.get("ANTHROPIC_API_KEY") == "sk-mm"


def test_profile_none_does_not_inject_provider_env(_mock_mgr):
    """When profile=None, AUTOSKILLIT_PROVIDER_PROFILE must NOT appear in env_extras."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {}
    captured = _run_cook(None, cfg, _mock_mgr)
    assert len(captured) >= 1, "build_interactive_cmd was not called"
    env = captured[0]
    assert "AUTOSKILLIT_PROVIDER_PROFILE" not in env


def test_profile_feature_disabled_exits(capsys, _mock_mgr):
    """SystemExit(1) with informative message when providers feature is not enabled."""
    cfg = MagicMock()
    cfg.experimental_enabled = False
    cfg.providers.profiles = {"minimax": {}}
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=_mock_mgr),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._session_cook.is_feature_enabled", return_value=False),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(profile="minimax", backend=mock_backend_cls())
    assert exc_info.value.code == 1
    assert "providers" in capsys.readouterr().err


def test_profile_unknown_exits(capsys, _mock_mgr):
    """SystemExit(1) with informative message listing known profiles for unknown name."""
    cfg = MagicMock()
    cfg.experimental_enabled = True
    cfg.providers.profiles = {"anthropic": {}, "openai": {}}
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=_mock_mgr),
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.cli.session._session_cook.is_feature_enabled", return_value=True),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cook_module.cook(profile="minimax", backend=mock_backend_cls())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "minimax" in err
    assert "anthropic" in err or "openai" in err


def test_cook_rejects_orchestrator_skill_in_l1_tier_before_launch() -> None:
    """Direct cook composition validates configured tiers before materialization."""
    cfg = AutomationConfig()
    cfg.skills.tier2 = ["process-issues"]
    mock_backend_cls, _ = _make_mock_backend_class()
    with (
        patch("autoskillit.config.load_config", return_value=cfg),
        patch("autoskillit.workspace.DefaultSessionSkillManager") as manager_cls,
        # project_dir comes from the shared git-toplevel helper; pin it so the
        # "nothing launched" assertion below stays about launches.
        patch("autoskillit.cli.session._session_cook.resolve_project_dir", Path.cwd),
        patch("subprocess.run") as run,
    ):
        with pytest.raises(SkillContractError, match="process-issues.*ORCHESTRATOR"):
            cook_module.cook(backend=mock_backend_cls())

    manager_cls.assert_not_called()
    run.assert_not_called()
