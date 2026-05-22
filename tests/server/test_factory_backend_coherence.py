"""Tests for backend coherence enforcement in make_context()."""

from __future__ import annotations

import pytest

from autoskillit.config import AgentBackendConfig, AutomationConfig
from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.server._factory import make_context
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _runner() -> MockSubprocessRunner:
    r = MockSubprocessRunner()
    r.set_default(
        SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
    )
    return r


def test_make_context_rejects_codex_backend_when_config_is_claude_code():
    """experimental_enabled=True must NOT swap backend when config says claude-code."""
    cfg = AutomationConfig(
        experimental_enabled=True,
        agent_backend=AgentBackendConfig(backend="claude-code"),
    )
    ctx = make_context(cfg, runner=_runner())
    assert ctx.backend is not None
    assert ctx.backend.name == "claude-code"
    assert isinstance(ctx.backend, ClaudeCodeBackend)


def test_make_context_allows_codex_backend_when_config_is_codex():
    """Explicit codex config + codex_backend feature flag should activate CodexBackend."""
    cfg = AutomationConfig(
        features={"codex_backend": True},
        agent_backend=AgentBackendConfig(backend="codex"),
    )
    ctx = make_context(cfg, runner=_runner())
    assert ctx.backend is not None
    assert ctx.backend.name == "codex"
    assert isinstance(ctx.backend, CodexBackend)


def test_experimental_enabled_does_not_promote_codex_when_config_is_claude():
    """experimental_enabled=True with claude-code config must keep ClaudeCodeBackend."""
    cfg = AutomationConfig(
        experimental_enabled=True,
        agent_backend=AgentBackendConfig(backend="claude-code"),
    )
    ctx = make_context(cfg, runner=_runner())
    assert isinstance(ctx.backend, ClaudeCodeBackend)
    assert ctx.backend.write_tool_names() == frozenset({"Write", "Edit"})


def test_codex_flag_ignored_warning_when_config_mismatch():
    """When codex_backend is explicitly enabled but config says claude-code, warn and skip."""
    import structlog.testing

    with structlog.testing.capture_logs() as logs:
        cfg = AutomationConfig(
            features={"codex_backend": True},
            agent_backend=AgentBackendConfig(backend="claude-code"),
        )
        ctx = make_context(cfg, runner=_runner())

    assert isinstance(ctx.backend, ClaudeCodeBackend)
    warning_logs = [rec for rec in logs if rec.get("event") == "codex_backend_flag_ignored"]
    assert len(warning_logs) == 1
    assert warning_logs[0]["configured_backend"] == "claude-code"
