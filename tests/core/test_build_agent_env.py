"""Alias contract tests for build_agent_env / build_claude_env."""

from __future__ import annotations

from types import MappingProxyType

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_build_agent_env_importable_from_core() -> None:
    from autoskillit.core import build_agent_env

    assert callable(build_agent_env)


def test_build_agent_env_is_alias_for_build_claude_env() -> None:
    from autoskillit.core._claude_env import build_agent_env, build_claude_env

    assert build_agent_env is build_claude_env


def test_build_claude_env_still_importable() -> None:
    from autoskillit.core import build_claude_env

    result = build_claude_env(base={"PATH": "/usr/bin"})
    assert "PATH" in result


def test_build_agent_env_returns_mappingproxy() -> None:
    from autoskillit.core import build_agent_env

    result = build_agent_env(base={"HOME": "/tmp"})
    assert isinstance(result, MappingProxyType)
    with pytest.raises(TypeError):
        result["X"] = "Y"  # type: ignore[index]


def test_build_agent_env_strips_ide_vars() -> None:
    from autoskillit.core import build_agent_env

    result = build_agent_env(base={"CLAUDE_CODE_SSE_PORT": "23270", "HOME": "/tmp"})
    assert "CLAUDE_CODE_SSE_PORT" not in result
    assert result["HOME"] == "/tmp"


def test_build_agent_env_applies_extras() -> None:
    from autoskillit.core import build_agent_env

    result = build_agent_env(base={}, extras={"AUTOSKILLIT_HEADLESS": "1"})
    assert result["AUTOSKILLIT_HEADLESS"] == "1"
