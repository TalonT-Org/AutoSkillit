"""Tests for codex_backend feature flag gating in make_context()."""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
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
    monkeypatch.setattr("autoskillit.server._factory.is_feature_enabled", lambda *a, **kw: False)
    from autoskillit.execution.backends.codex import CodexBackend

    ctx = make_context(AutomationConfig(), runner=_runner())
    assert not isinstance(ctx.backend, CodexBackend)


def test_codex_backend_instantiated_when_enabled(monkeypatch):
    """When codex_backend feature is enabled, ctx.backend is a CodexBackend instance."""
    monkeypatch.setattr("autoskillit.server._factory.is_feature_enabled", lambda *a, **kw: True)
    from autoskillit.execution.backends.codex import CodexBackend

    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx.backend, CodexBackend)
