"""make_context() project-directory resolution, fixture gate defaults, backend selection, and persistent roots."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.config import AgentBackendConfig, AutomationConfig
from autoskillit.core import AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR
from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.execution.recording import RecordingSubprocessRunner
from autoskillit.server._factory import make_context
from tests.server._factory_test_helpers import _runner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture()
def _register_aider_backend(monkeypatch):
    import dataclasses

    from autoskillit.execution.backends import BACKEND_REGISTRY, CodexBackend

    class _NonReplayBackend(CodexBackend):
        @property
        def capabilities(self):
            return dataclasses.replace(super().capabilities, replay_capable=False)

    monkeypatch.setitem(BACKEND_REGISTRY, "aider", _NonReplayBackend)


def test_make_context_uses_explicit_project_dir(tmp_path):
    """make_context() uses project_dir when passed explicitly."""
    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert ctx.project_dir == tmp_path


def test_serve_passes_project_dir_env_to_make_context(monkeypatch, tmp_path):
    """serve() reads AUTOSKILLIT_PROJECT_DIR and passes it as project_dir to make_context()."""
    captured: dict = {}

    def fake_make_context(cfg, **kwargs):
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr("autoskillit.server.make_context", fake_make_context)

    from autoskillit.cli.app import serve

    with pytest.raises(SystemExit):
        serve()

    assert captured.get("project_dir") == tmp_path


def test_serve_normalizes_empty_audit_authority_before_context_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict = {}

    def fake_make_context(cfg, **kwargs):
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setenv("AUTOSKILLIT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv(AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR, "")
    monkeypatch.setattr("autoskillit.server.make_context", fake_make_context)

    from autoskillit.cli.app import serve

    with pytest.raises(SystemExit):
        serve()

    assert captured["audit_admission_store_authority"] is None


def test_make_context_project_dir_git_root_fallback(monkeypatch, tmp_path):
    """make_context() without explicit project_dir falls back to git toplevel."""
    import subprocess as _subprocess

    git_root = tmp_path / "git-root"
    git_root.mkdir()

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 0, stdout=f"{git_root}\n", stderr="")

    monkeypatch.setattr("autoskillit.core.paths.subprocess.run", fake_run)
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.project_dir == git_root


def test_resolve_project_dir_git_root(monkeypatch, tmp_path):
    import subprocess as _subprocess

    from autoskillit.core import resolve_project_dir

    git_root = tmp_path / "git-root"
    git_root.mkdir()

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 0, stdout=f"{git_root}\n", stderr="")

    monkeypatch.setattr("autoskillit.core.paths.subprocess.run", fake_run)
    assert resolve_project_dir() == git_root


def test_resolve_project_dir_ignores_a_toplevel_that_is_not_a_directory(monkeypatch):
    """A toplevel that does not exist is not a project root — fall back to cwd.

    Real git never returns one, but a test that mocks subprocess.run wholesale
    does, and the resulting garbage path used to be materialised on disk by the
    first mkdir(parents=True) downstream.
    """
    import subprocess as _subprocess

    from autoskillit.core import resolve_project_dir

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 0, stdout="/no/such/toplevel\n", stderr="")

    monkeypatch.setattr("autoskillit.core.paths.subprocess.run", fake_run)
    assert resolve_project_dir() == Path.cwd()


def test_resolve_project_dir_cwd_fallback(monkeypatch):
    import subprocess as _subprocess

    from autoskillit.core import resolve_project_dir

    def fake_run(cmd, *, capture_output, text, timeout):
        return _subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not a git repo")

    monkeypatch.setattr("autoskillit.core.paths.subprocess.run", fake_run)
    assert resolve_project_dir() == Path.cwd()


def test_make_context_skips_replay_runner_for_non_claude_backend(
    monkeypatch, tmp_path, _register_aider_backend
):
    monkeypatch.setenv("REPLAY_SCENARIO", "1")
    monkeypatch.setenv("REPLAY_SCENARIO_DIR", str(tmp_path))
    monkeypatch.delenv("RECORD_SCENARIO", raising=False)

    mock_build = Mock()
    monkeypatch.setattr("autoskillit.server._factory.build_replay_runner", mock_build)

    cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="aider"))
    ctx = make_context(cfg, plugin_dir=str(tmp_path), project_dir=tmp_path)

    assert mock_build.call_count == 0
    assert isinstance(ctx.runner, DefaultSubprocessRunner)


def test_make_context_skips_record_runner_for_non_claude_backend(
    monkeypatch, tmp_path, _register_aider_backend
):
    monkeypatch.setenv("RECORD_SCENARIO", "1")
    monkeypatch.setenv("RECORD_SCENARIO_DIR", str(tmp_path))
    monkeypatch.delenv("REPLAY_SCENARIO", raising=False)

    cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="aider"))
    ctx = make_context(cfg, plugin_dir=str(tmp_path), project_dir=tmp_path)

    assert isinstance(ctx.runner, DefaultSubprocessRunner)
    assert not isinstance(ctx.runner, RecordingSubprocessRunner)


def test_make_context_backend_is_coding_agent_backend(tmp_path) -> None:
    """make_context() sets ctx.backend to a CodingAgentBackend instance."""
    from autoskillit.core import CodingAgentBackend

    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx.backend, CodingAgentBackend)


def test_make_context_default_backend_is_claude_code(tmp_path) -> None:
    """Default config (agent_backend.backend='claude-code') produces ClaudeCodeBackend."""
    from autoskillit.execution.backends import ClaudeCodeBackend

    ctx = make_context(AutomationConfig(), runner=_runner(), project_dir=tmp_path)
    assert isinstance(ctx.backend, ClaudeCodeBackend)


def test_make_context_unknown_backend_raises_value_error(tmp_path) -> None:
    """Unknown agent_backend key raises ValueError with supported keys."""
    cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="nonexistent"))
    with pytest.raises(ValueError, match="nonexistent"):
        make_context(cfg, runner=_runner(), project_dir=tmp_path)


def test_make_context_codex_backend_not_none_plain_config(tmp_path) -> None:
    """AgentBackendConfig(backend='codex') with no feature flags produces CodexBackend."""
    from autoskillit.execution.backends.codex import CodexBackend

    cfg = AutomationConfig(agent_backend=AgentBackendConfig(backend="codex"))
    ctx = make_context(cfg, runner=_runner(), project_dir=tmp_path)
    assert ctx.backend is not None
    assert isinstance(ctx.backend, CodexBackend)


def test_make_context_builds_persistent_roots_over_all_registered_backends(tmp_path) -> None:
    """T4 (#4391): make_context() derives persistent_roots for every backend, not
    just the configured global one — proving the fix's core wiring."""
    from autoskillit.core import CODEX_SESSIONS_SUBDIR, resolve_temp_dir

    config = AutomationConfig()
    ctx = make_context(config, runner=_runner(), project_dir=tmp_path)

    expected_root = resolve_temp_dir(tmp_path, config.workspace.temp_dir) / CODEX_SESSIONS_SUBDIR
    assert ctx.session_skill_manager._persistent_roots == {"codex": expected_root}
