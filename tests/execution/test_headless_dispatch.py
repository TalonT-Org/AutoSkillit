"""Tests for headless.py dispatch flow: food truck dispatch, pack injection, executor protocol."""

import json
from pathlib import Path

import pytest

from autoskillit.core.types._type_plugin_source import DirectInstall, MarketplaceInstall

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_success_stdout(marker: str = "%%FT_DONE%%") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": f"L2 done {marker}",
            "session_id": "ft-session",
            "is_error": False,
        }
    )


class TestDispatchFoodTruck:
    """Tests for DefaultHeadlessExecutor.dispatch_food_truck."""

    @pytest.mark.anyio
    async def test_dispatch_food_truck_calls_runner(self, minimal_ctx, tmp_path: Path):
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
        )

        assert runner.call_args_list, "runner was never called"
        cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        assert env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert "--tools" in cmd
        assert "AskUserQuestion" in cmd

    @pytest.mark.anyio
    async def test_dispatch_food_truck_returns_skill_result(self, minimal_ctx, tmp_path: Path):
        from autoskillit.core.types import SkillResult, SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        result = await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
        )

        assert isinstance(result, SkillResult)
        assert result.success is True

    @pytest.mark.anyio
    async def test_dispatch_food_truck_on_spawn_receives_pid(self, minimal_ctx, tmp_path: Path):
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)

        spawned_pids: list[int] = []

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            on_spawn=lambda pid, ticks: spawned_pids.append(pid),
        )

        assert spawned_pids == [55555]

    @pytest.mark.anyio
    async def test_dispatch_food_truck_on_spawn_not_required(self, minimal_ctx, tmp_path: Path):
        from autoskillit.core.types import SkillResult, SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        result = await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            on_spawn=None,
        )

        assert isinstance(result, SkillResult)
        assert result.success is True

    @pytest.mark.anyio
    async def test_dispatch_food_truck_marketplace_install_does_not_raise(
        self, minimal_ctx, tmp_path: Path
    ):
        """dispatch_food_truck with MarketplaceInstall resolves cache_path — no ValueError."""
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        cache = tmp_path / "marketplace_cache"
        cache.mkdir()
        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = MarketplaceInstall(cache_path=cache)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
        )
        cmd, _cwd, _timeout, _kwargs = runner.call_args_list[0]
        assert "--plugin-dir" in cmd
        assert str(cache) in cmd


class TestDispatchFoodTruckPackInjection:
    """Tests that dispatch_food_truck correctly injects AUTOSKILLIT_L2_TOOL_TAGS."""

    @pytest.mark.anyio
    async def test_requires_packs_injected_as_l2_tool_tags(self, minimal_ctx, tmp_path: Path):
        """dispatch_food_truck with requires_packs injects sorted comma-joined env var."""
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            requires_packs=["ci", "github", "clone"],
        )

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        assert env["AUTOSKILLIT_L2_TOOL_TAGS"] == "ci,clone,github"

    @pytest.mark.anyio
    async def test_requires_packs_empty_omits_l2_tool_tags(self, minimal_ctx, tmp_path: Path):
        """dispatch_food_truck with empty requires_packs does not inject L2_TOOL_TAGS."""
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L2 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            requires_packs=[],
        )

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        assert "AUTOSKILLIT_L2_TOOL_TAGS" not in env


class TestDispatchFoodTruckGuards:
    """Guard-path tests for dispatch_food_truck: conflict detection and skip_clone_guard."""

    @pytest.mark.anyio
    async def test_dispatch_food_truck_l2_tool_tags_conflict_raises(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.core.types._type_plugin_source import DirectInstall
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        with pytest.raises(ValueError, match="AUTOSKILLIT_L2_TOOL_TAGS"):
            await executor.dispatch_food_truck(
                "some prompt",
                str(tmp_path),
                completion_marker="DONE",
                requires_packs=["ci"],
                env_extras={"AUTOSKILLIT_L2_TOOL_TAGS": "ci"},
            )

    @pytest.mark.anyio
    async def test_dispatch_food_truck_skip_clone_guard_prevents_snapshot(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.core.types._type_plugin_source import DirectInstall
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        mock_snapshot = AsyncMock()
        monkeypatch.setattr(
            "autoskillit.execution.headless.snapshot_clone_state",
            mock_snapshot,
        )

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=12345,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        await executor.dispatch_food_truck(
            "some prompt",
            str(tmp_path),
            completion_marker="DONE",
        )

        assert mock_snapshot.call_count == 0


def test_default_executor_satisfies_protocol_with_dispatch(minimal_ctx) -> None:
    """DefaultHeadlessExecutor satisfies HeadlessExecutor protocol with dispatch_food_truck."""
    from autoskillit.core import HeadlessExecutor
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    executor = DefaultHeadlessExecutor(minimal_ctx)
    assert isinstance(executor, HeadlessExecutor)
