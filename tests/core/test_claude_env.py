"""Unit tests for build_claude_env() — IDE env scrubbing at the subprocess launch boundary."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from autoskillit.core import build_claude_env
from autoskillit.core._claude_env import (
    IDE_ENV_ALWAYS_EXTRAS,
    IDE_ENV_DENYLIST,
    IDE_ENV_PREFIX_DENYLIST,
)


def test_build_claude_env_strips_sse_port() -> None:
    base = {"CLAUDE_CODE_SSE_PORT": "23270", "HOME": "/tmp", "PATH": "/usr/bin"}
    result = build_claude_env(base=base)
    assert "CLAUDE_CODE_SSE_PORT" not in result
    assert result["HOME"] == "/tmp"
    assert result["PATH"] == "/usr/bin"


@pytest.mark.parametrize(
    "key",
    [
        "CLAUDE_CODE_IDE_HOST_OVERRIDE",
        "CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL",
        "CLAUDE_CODE_IDE_SKIP_VALID_CHECK",
        "CLAUDE_CODE_SSE_TOKEN",
        "CLAUDE_CODE_SSE_HOST",
    ],
)
def test_build_claude_env_strips_ide_prefix(key: str) -> None:
    result = build_claude_env(base={key: "value", "HOME": "/tmp"})
    assert key not in result
    assert "HOME" in result


@pytest.mark.parametrize(
    "key",
    [
        "CLAUDE_CODE_SSE_PORT",
        "ENABLE_IDE_INTEGRATION",
        "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
        "VSCODE_GIT_ASKPASS_MAIN",
        "CURSOR_TRACE_ID",
        "ZED_TERM",
    ],
)
def test_build_claude_env_strips_expanded_denylist(key: str) -> None:
    result = build_claude_env(base={key: "value", "HOME": "/tmp"})
    assert key not in result
    assert "HOME" in result


def test_build_claude_env_preserves_unrelated_claude_vars() -> None:
    base = {
        "CLAUDE_CONFIG_DIR": "/home/user/.claude",
        "ANTHROPIC_API_KEY": "sk-...",
        "ANTHROPIC_LOG": "debug",
    }
    result = build_claude_env(base=base)
    assert result["CLAUDE_CONFIG_DIR"] == "/home/user/.claude"
    assert result["ANTHROPIC_API_KEY"] == "sk-..."
    assert result["ANTHROPIC_LOG"] == "debug"


def test_build_claude_env_injects_auto_connect_off() -> None:
    result = build_claude_env(base={})
    assert result["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"


def test_build_claude_env_caller_extras_can_override_auto_connect() -> None:
    result = build_claude_env(base={}, extras={"CLAUDE_CODE_AUTO_CONNECT_IDE": "1"})
    assert result["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "1"


def test_build_claude_env_applies_extras() -> None:
    result = build_claude_env(base={}, extras={"AUTOSKILLIT_HEADLESS": "1"})
    assert result["AUTOSKILLIT_HEADLESS"] == "1"


def test_build_claude_env_extras_override_base() -> None:
    result = build_claude_env(base={"FOO": "original"}, extras={"FOO": "overridden"})
    assert result["FOO"] == "overridden"


def test_build_claude_env_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEST_DEFAULT_ENV_MARKER", "present")
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
    result = build_claude_env()
    assert result.get("AUTOSKILLIT_TEST_DEFAULT_ENV_MARKER") == "present"
    assert "CLAUDE_CODE_SSE_PORT" not in result


def test_build_claude_env_returns_mappingproxy() -> None:
    result = build_claude_env(base={"HOME": "/tmp"})
    assert isinstance(result, MappingProxyType)
    with pytest.raises(TypeError):
        result["X"] = "Y"  # type: ignore[index]


def test_ide_env_denylist_contains_expected_names() -> None:
    assert "CLAUDE_CODE_SSE_PORT" in IDE_ENV_DENYLIST
    assert "ENABLE_IDE_INTEGRATION" in IDE_ENV_DENYLIST
    assert "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR" in IDE_ENV_DENYLIST
    assert "VSCODE_GIT_ASKPASS_MAIN" in IDE_ENV_DENYLIST
    assert "CURSOR_TRACE_ID" in IDE_ENV_DENYLIST
    assert "ZED_TERM" in IDE_ENV_DENYLIST


def test_ide_env_prefix_denylist_covers_ide_and_sse() -> None:
    assert "CLAUDE_CODE_IDE_" in IDE_ENV_PREFIX_DENYLIST
    assert "CLAUDE_CODE_SSE" in IDE_ENV_PREFIX_DENYLIST


def test_ide_env_always_extras_includes_auto_connect_off() -> None:
    assert IDE_ENV_ALWAYS_EXTRAS["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"
