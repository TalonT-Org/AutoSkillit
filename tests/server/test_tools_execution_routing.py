"""Tests for run_skill routing, executor delegation, and session skill management."""

from __future__ import annotations

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_tools_execution_routes_through_executor(tool_ctx, monkeypatch) -> None:
    """run_skill routes through ctx.executor.run(), not run_headless_core directly."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/test skill", "/tmp")
    assert len(executor.calls) == 1
    assert executor.calls[0].skill_command == "/test skill"
    assert executor.calls[0].cwd == "/tmp"


@pytest.mark.anyio
async def test_run_skill_passes_validated_add_dirs(tool_ctx, monkeypatch) -> None:
    """run_skill passes ValidatedAddDir instances (not raw strings) as add_dirs."""
    from autoskillit.core import ValidatedAddDir
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/test skill", "/tmp")
    # All add_dirs must be ValidatedAddDir instances
    assert len(executor.calls[0].add_dirs) >= 1
    assert all(isinstance(d, ValidatedAddDir) for d in executor.calls[0].add_dirs)
    # Must not include raw skills_extended/ path
    from autoskillit.workspace.skills import bundled_skills_extended_dir

    skills_ext = str(bundled_skills_extended_dir())
    add_dir_paths = [d.path for d in executor.calls[0].add_dirs]
    assert skills_ext not in add_dir_paths


@pytest.mark.anyio
async def test_run_skill_calls_session_skill_manager_init_session(tool_ctx, monkeypatch) -> None:
    """run_skill routes through session_skill_manager.init_session() for add_dirs."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir

    # Create a spy on init_session
    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm

    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/test skill", "/tmp")

    # init_session was called with cook_session=False (headless, not cook)
    mock_ssm.init_session.assert_called_once()
    call_kwargs = mock_ssm.init_session.call_args
    assert call_kwargs.kwargs.get("cook_session") is False

    # The returned ValidatedAddDir is in add_dirs
    assert fake_validated in executor.calls[0].add_dirs


@pytest.mark.anyio
async def test_run_skill_activates_deps_for_tier3_target(tool_ctx, monkeypatch) -> None:
    """run_skill calls activate_skill_deps even when target is tier3 (not in tier2 list)."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir

    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm

    # Set up skill_resolver to produce a resolved name
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx.skill_resolver = mock_resolver

    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    # Use a tier3 skill name
    await run_skill("/open-pr", "/tmp")

    # activate_skill_deps must have been called regardless of tier
    mock_ssm.activate_skill_deps.assert_called_once()


@pytest.mark.anyio
async def test_run_skill_result_includes_order_id_when_passed(tool_ctx, monkeypatch) -> None:
    """run_skill injects order_id into the result JSON when order_id is non-empty."""
    import json as _json

    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    result_json = await run_skill("/test skill", "/tmp", order_id="issue-185")
    data = _json.loads(result_json)
    assert data.get("order_id") == "issue-185"


@pytest.mark.anyio
async def test_run_skill_result_order_id_empty_string_when_not_passed(
    tool_ctx, monkeypatch
) -> None:
    """run_skill emits order_id as empty string in result JSON when none provided."""
    import json as _json

    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    result_json = await run_skill("/test skill", "/tmp")  # no order_id
    data = _json.loads(result_json)
    assert data.get("order_id") == ""


@pytest.mark.anyio
async def test_run_skill_passes_allow_only_to_init_session(tool_ctx, monkeypatch) -> None:
    """run_skill computes the closure for the resolved target and forwards it as allow_only."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir
    from tests.fakes import InMemoryHeadlessExecutor

    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    expected_closure = frozenset({"investigate", "mermaid"})

    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    mock_ssm.compute_skill_closure.return_value = expected_closure
    tool_ctx.session_skill_manager = mock_ssm

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx.skill_resolver = mock_resolver

    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/autoskillit:investigate the bug", "/tmp")

    mock_ssm.compute_skill_closure.assert_called_once_with("investigate")
    mock_ssm.init_session.assert_called_once()
    assert mock_ssm.init_session.call_args.kwargs.get("allow_only") == expected_closure


@pytest.mark.anyio
async def test_run_skill_no_target_skill_passes_none_allow_only(tool_ctx, monkeypatch) -> None:
    """When skill_resolver is unset, target_name is None and allow_only stays None."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir
    from tests.fakes import InMemoryHeadlessExecutor

    fake_validated = ValidatedAddDir(path="/fake/session/dir")
    mock_ssm = MagicMock()
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx.session_skill_manager = mock_ssm
    tool_ctx.skill_resolver = None  # disables resolve_target_skill

    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/test skill", "/tmp")

    mock_ssm.init_session.assert_called_once()
    assert mock_ssm.init_session.call_args.kwargs.get("allow_only") is None
    mock_ssm.compute_skill_closure.assert_not_called()


@pytest.mark.anyio
async def test_run_skill_make_plan_closure_includes_arch_lens_pack(tool_ctx, monkeypatch) -> None:
    """End-to-end: /make-plan resolves a closure containing the entire arch-lens pack."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from tests.fakes import InMemoryHeadlessExecutor

    real_provider = SkillsDirectoryProvider()
    real_mgr = DefaultSessionSkillManager(provider=real_provider, ephemeral_root=tool_ctx.temp_dir)

    captured: dict = {}

    class _RecordingManager:
        def __init__(self, real: DefaultSessionSkillManager) -> None:
            self._real = real

        def init_session(self, session_id, **kwargs):
            captured["allow_only"] = kwargs.get("allow_only")
            return ValidatedAddDir(path="/fake/session/dir")

        def compute_skill_closure(self, target_name):
            return self._real.compute_skill_closure(target_name)

        def activate_skill_deps(self, session_id, skill_name):
            return True

        def cleanup_stale(self, max_age_seconds=86400):
            return 0

    tool_ctx.session_skill_manager = _RecordingManager(real_mgr)

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = MagicMock(source=MagicMock(value="bundled_extended"))
    tool_ctx.skill_resolver = mock_resolver

    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/autoskillit:make-plan refactor", "/tmp")

    closure = captured["allow_only"]
    assert closure is not None
    assert "make-plan" in closure
    assert "mermaid" in closure
    arch_members = {n for n in closure if n.startswith("arch-lens-")}
    assert len(arch_members) >= 1


@pytest.mark.anyio
async def test_run_skill_passes_idle_output_timeout(tool_ctx, monkeypatch) -> None:
    """run_skill passes idle_output_timeout (as float) to executor.run()."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/test skill", "/tmp", idle_output_timeout=120)
    assert executor.calls[0].idle_output_timeout == 120.0  # int→float conversion


@pytest.mark.anyio
async def test_run_skill_idle_output_timeout_defaults_to_none(tool_ctx, monkeypatch) -> None:
    """run_skill passes None to executor.run() when idle_output_timeout is not set."""
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    await run_skill("/test skill", "/tmp")
    assert executor.calls[0].idle_output_timeout is None


class TestOutputDirParameter:
    """output_dir parameter plumbing from run_skill to executor."""

    def test_run_skill_has_output_dir_parameter(self) -> None:
        """run_skill() accepts output_dir parameter."""
        import inspect

        sig = inspect.signature(run_skill)
        assert "output_dir" in sig.parameters
        param = sig.parameters["output_dir"]
        assert param.default == ""

    @pytest.mark.anyio
    async def test_run_skill_forwards_output_dir_to_write_watch_dirs(
        self, tool_ctx, monkeypatch, tmp_path
    ) -> None:
        """output_dir is resolved and forwarded to executor.run() as write_watch_dirs."""
        from pathlib import Path

        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx.executor = executor
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

        output_dir = str(tmp_path / "output")
        await run_skill("/test skill", str(tmp_path), output_dir=output_dir)

        assert len(executor.calls) == 1
        assert Path(output_dir) in executor.calls[0].write_watch_dirs
