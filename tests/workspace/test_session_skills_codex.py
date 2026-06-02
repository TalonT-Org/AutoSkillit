"""Tests for Codex-specific session skill layout delegation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ClaudeDirectoryConventions, ValidatedAddDir
from tests.workspace._helpers import _CODEX_CAPABILITIES

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _make_codex_backend() -> MagicMock:
    b = MagicMock()
    b.capabilities = _CODEX_CAPABILITIES
    b.conventions.skills_subdir = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    b.ensure_pre_launch.return_value = []
    return b


@pytest.fixture
def codex_env():
    """Codex backend mock for delegation-contract tests."""
    backend = _make_codex_backend()

    return type(
        "CodexEnv",
        (),
        {
            "backend": backend,
        },
    )()


def test_codex_init_session_creates_skills_subdir(make_session_skill_manager, codex_env) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    skill_files = list(
        (session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR).glob("*/SKILL.md")
    )
    assert len(skill_files) > 0
    assert not (session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR).exists()


def test_codex_init_session_delegates_to_setup_session_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    codex_env.backend.setup_session_dir.assert_called_once_with(Path(str(session_path)))


def test_no_backend_skips_setup_session_dir(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    result = mgr.init_session("sid", cook_session=True)
    assert isinstance(result, ValidatedAddDir)


def test_codex_init_session_returns_validated_add_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    result = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert isinstance(result, ValidatedAddDir)
    assert str(result).endswith("/sid")


def test_claude_backend_still_uses_dot_claude_layout(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True)
    skill_files = list(
        (session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR).glob("*/SKILL.md")
    )
    assert len(skill_files) > 0
    assert not (session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR).exists()


def test_codex_init_session_calls_ensure_pre_launch(make_session_skill_manager, codex_env) -> None:
    """init_session() must call backend.ensure_pre_launch() when mcp_config_capable is True."""
    pre_launch_called: list[bool] = []
    codex_env.backend.ensure_pre_launch.return_value = []
    codex_env.backend.ensure_pre_launch.side_effect = lambda: pre_launch_called.append(True) or []

    mgr = make_session_skill_manager()
    mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert pre_launch_called, "ensure_pre_launch() must be called during init_session"


def test_codex_init_session_raises_when_pre_launch_fails(
    make_session_skill_manager, codex_env
) -> None:
    """init_session() must raise RuntimeError when ensure_pre_launch() returns errors."""
    codex_env.backend.ensure_pre_launch.return_value = ["Failed to ensure MCP registration: err"]

    mgr = make_session_skill_manager()
    with pytest.raises(RuntimeError, match="Pre-launch check failed"):
        mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
