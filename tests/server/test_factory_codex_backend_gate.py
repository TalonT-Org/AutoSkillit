"""Tests for codex_backend feature flag gating in make_context()."""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import is_feature_enabled
from autoskillit.core.types import SubprocessResult, TerminationReason
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


def test_codex_backend_not_instantiated_when_disabled(monkeypatch):
    """When codex_backend feature is disabled, CodexBackend is not the ctx.backend."""
    monkeypatch.setattr(
        "autoskillit.server._factory.is_feature_enabled",
        lambda name, *a, **kw: (
            False if name == "codex_backend" else is_feature_enabled(name, *a, **kw)
        ),
    )
    from autoskillit.execution.backends.codex import CodexBackend

    ctx = make_context(AutomationConfig(), runner=_runner())
    assert not isinstance(ctx.backend, CodexBackend)


def test_codex_backend_instantiated_when_enabled(monkeypatch):
    """When codex_backend is enabled and config backend is codex, ctx.backend is CodexBackend."""
    monkeypatch.setattr(
        "autoskillit.server._factory.is_feature_enabled",
        lambda name, *a, **kw: (
            True if name == "codex_backend" else is_feature_enabled(name, *a, **kw)
        ),
    )
    from autoskillit.config._config_dataclasses import AgentBackendConfig
    from autoskillit.execution.backends.codex import CodexBackend

    config = AutomationConfig()
    config.agent_backend = AgentBackendConfig(backend="codex")
    ctx = make_context(config, runner=_runner())
    assert isinstance(ctx.backend, CodexBackend)
