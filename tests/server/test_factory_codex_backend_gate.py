"""Tests for codex_backend feature flag gating in make_context()."""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import is_feature_enabled
from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.server import _factory
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


def test_codex_backend_not_instantiated_when_disabled(monkeypatch, tmp_path):
    """When codex_backend feature is disabled, CodexBackend is not the ctx.backend."""
    monkeypatch.setattr(
        _factory,
        "is_feature_enabled",
        lambda name, *a, **kw: (
            False if name == "codex_backend" else is_feature_enabled(name, *a, **kw)
        ),
    )
    from autoskillit.execution.backends.codex import CodexBackend

    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert not isinstance(ctx.backend, CodexBackend)


def test_codex_backend_instantiated_when_enabled(monkeypatch, tmp_path):
    """When codex_backend is enabled and config backend is codex, ctx.backend is CodexBackend."""
    monkeypatch.setattr(
        _factory,
        "is_feature_enabled",
        lambda name, *a, **kw: (
            True if name == "codex_backend" else is_feature_enabled(name, *a, **kw)
        ),
    )
    from autoskillit.config._config_dataclasses import AgentBackendConfig
    from autoskillit.execution.backends.codex import CodexBackend

    config = AutomationConfig()
    config.agent_backend = AgentBackendConfig(backend="codex")
    ctx = make_context(config, runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx.backend, CodexBackend)


def test_codex_runtime_spec_is_shared_by_factory_backend_and_resolver(tmp_path):
    from autoskillit.config import CodexRuntimeConfig
    from autoskillit.config._config_dataclasses import AgentBackendConfig
    from autoskillit.core import BackendAuthority, BackendAuthorityKind, BackendAuthorityTier
    from autoskillit.execution.backends.codex import CodexBackend

    config = AutomationConfig(
        agent_backend=AgentBackendConfig(backend="codex"),
        codex_runtime=CodexRuntimeConfig(context_window_tokens=200_000),
    )
    ctx = make_context(config, runner=_runner(), project_dir=tmp_path)
    resolved = ctx.launch_resolver.backend_for_authority(
        BackendAuthority(
            backend="codex",
            kind=BackendAuthorityKind.GLOBAL,
            tier=BackendAuthorityTier.GLOBAL,
            key_path="agent_backend.backend",
        )
    )

    assert isinstance(ctx.backend, CodexBackend)
    assert isinstance(resolved, CodexBackend)
    assert ctx.backend.runtime_spec is resolved.runtime_spec
    assert resolved.runtime_spec.context_window_tokens == 200_000
