"""Tests for headless.py dispatch flow: food truck dispatch, pack injection, executor protocol."""

import json
from pathlib import Path

import pytest

from autoskillit.core import PluginArtifactIdentity, PluginLaunchBinding
from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _Lease:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StaticPluginAuthority:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir.resolve()
        self.leases: list[_Lease] = []

    def acquire_launch_binding(self, *, backend, load_mode):
        lease = _Lease()
        self.leases.append(lease)
        return PluginLaunchBinding(
            load_mode=load_mode,
            plugin_dir=self.plugin_dir,
            identity=PluginArtifactIdentity(
                semantic_key="test-plugin",
                incarnation_id=f"test-{len(self.leases)}",
                manifest_schema_version=1,
                artifact_digest="test-digest",
                managed_path=self.plugin_dir,
                manifest_path=self.plugin_dir.parent / "test-plugin.manifest.json",
            ),
            inherited_fds=(),
            _lease=lease,
        )


def _make_success_stdout(marker: str = "%%FT_DONE%%") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": f"L3 done {marker}",
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
        authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.plugin_authority = authority
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            plugin_authority=minimal_ctx.plugin_authority,
        )

        assert runner.call_args_list, "runner was never called"
        cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        assert env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"
        assert env["AUTOSKILLIT_HEADLESS"] == "1"
        assert env["TERM"] == "dumb"
        assert env["NO_COLOR"] == "1"
        assert runner.last_pty_mode is False
        assert "--tools" in cmd
        assert "AskUserQuestion" in cmd
        assert len(authority.leases) == 1
        assert authority.leases[0].closed is True

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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        result = await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        spawned_pids: list[int] = []

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        result = await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            on_spawn=None,
        )

        assert isinstance(result, SkillResult)
        assert result.success is True

    @pytest.mark.anyio
    async def test_dispatch_food_truck_emits_projected_plugin_dir(
        self, minimal_ctx, tmp_path: Path
    ):
        """Food-truck dispatch does not raise and does emit --plugin-dir.

        Regression guard for the leak this replaced: dispatch used to receive an
        *unprojected* source, hit the canonical-root guard, and was routed around
        it by passing the raw marketplace cache path straight to --plugin-dir. The
        source is now projected by construction, so the flag is emitted from a
        sanitized path with no bypass.
        """
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        projected = tmp_path / "projected-plugin"
        projected.mkdir()
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(projected)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            plugin_authority=minimal_ctx.plugin_authority,
        )
        cmd, _cwd, _timeout, _kwargs = runner.call_args_list[0]
        assert "--plugin-dir" in cmd
        assert str(projected) in cmd

    @pytest.mark.anyio
    async def test_dispatch_finalizes_semantics_while_binding_is_live(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
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
        authority = _StaticPluginAuthority(tmp_path)
        finalized_bindings: list[PluginLaunchBinding] = []

        class Preparation:
            def finalize(self, *, backend, binding):
                assert backend.name == "claude-code"
                assert binding.closed is False
                finalized_bindings.append(binding)
                return None

        minimal_ctx.runner = runner
        minimal_ctx.backend = ClaudeCodeBackend()
        minimal_ctx.plugin_authority = authority

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            plugin_authority=authority,
            capability_preparation=Preparation(),
        )

        assert len(finalized_bindings) == 1
        assert finalized_bindings[0].closed is True

    @pytest.mark.anyio
    async def test_dispatch_food_truck_passes_resume_session_id_to_cmd_builder(
        self, minimal_ctx, tmp_path: Path
    ):
        """resume_session_id is forwarded from dispatch_food_truck to build_food_truck_cmd."""
        from dataclasses import replace
        from unittest.mock import Mock

        from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        backend = Mock()
        backend.name = "claude-code"
        backend.capabilities = replace(CLAUDE_CODE_CAPABILITIES)
        backend.build_food_truck_cmd.return_value = CmdSpec(
            cmd=("claude", "--print", "test-prompt"), env={}
        )
        backend.write_tool_names.return_value = frozenset({"Write", "Edit"})
        minimal_ctx.backend = backend

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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            resume_session_id="abc-123",
        )

        backend.build_food_truck_cmd.assert_called_once()
        call_kwargs = backend.build_food_truck_cmd.call_args[1]
        assert call_kwargs["resume_session_id"] == "abc-123"


class TestDispatchFoodTruckPackInjection:
    """Tests that dispatch_food_truck correctly injects AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS."""

    @pytest.mark.anyio
    async def test_requires_packs_injected_as_l3_tool_tags(self, minimal_ctx, tmp_path: Path):
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            requires_packs=["ci", "github", "clone"],
        )

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        assert env["AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"] == "ci,clone,github"

    @pytest.mark.anyio
    async def test_requires_packs_empty_omits_l3_tool_tags(self, minimal_ctx, tmp_path: Path):
        """dispatch_food_truck with empty requires_packs does not inject FOOD_TRUCK_TOOL_TAGS."""
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
            requires_packs=[],
        )

        assert runner.call_args_list, "runner was never called"
        _cmd, _cwd, _timeout, kwargs = runner.call_args_list[0]
        env = kwargs.get("env")
        assert env is not None
        assert "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS" not in env


class TestDispatchFoodTruckGuards:
    """Guard-path tests for dispatch_food_truck: conflict detection and skip_clone_guard."""

    @pytest.mark.anyio
    async def test_dispatch_food_truck_l3_tool_tags_conflict_raises(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        with pytest.raises(ValueError, match="AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"):
            await executor.dispatch_food_truck(
                "some prompt",
                str(tmp_path),
                completion_marker="DONE",
                requires_packs=["ci"],
                env_extras={"AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS": "ci"},
            )

    @pytest.mark.anyio
    async def test_dispatch_food_truck_skip_clone_guard_prevents_snapshot(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        mock_snapshot = AsyncMock()
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute.snapshot_clone_state",
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()
        executor = DefaultHeadlessExecutor(minimal_ctx)

        await executor.dispatch_food_truck(
            "some prompt",
            str(tmp_path),
            completion_marker="DONE",
        )

        assert mock_snapshot.call_count == 0

    @pytest.mark.anyio
    async def test_dispatch_food_truck_raises_for_non_claude_code_backend(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.execution.conftest import _mock_backend

        minimal_ctx.backend = _mock_backend(food_truck_capable=False)
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        with pytest.raises(RuntimeError, match="food_truck_capable"):
            await executor.dispatch_food_truck(
                "some prompt",
                str(tmp_path),
                completion_marker="DONE",
            )

    @pytest.mark.anyio
    async def test_dispatch_food_truck_raises_for_none_backend(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        minimal_ctx.backend = None
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        with pytest.raises(RuntimeError, match="dispatch_backend must be resolved"):
            await executor.dispatch_food_truck(
                "some prompt",
                str(tmp_path),
                completion_marker="%%FT_DONE%%",
            )

    @pytest.mark.anyio
    async def test_dispatch_food_truck_allows_claude_code_backend(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.execution.conftest import _mock_backend
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = _mock_backend(
            food_truck_capable=True, pty_required=True, channel_b_capable=True
        )

        executor = DefaultHeadlessExecutor(minimal_ctx)
        result = await executor.dispatch_food_truck(
            "some prompt",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
        )
        assert result is not None
        assert len(runner.call_args_list) >= 1

    @pytest.mark.anyio
    async def test_dispatch_food_truck_raises_when_food_truck_capable_false(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.execution.conftest import _mock_backend

        minimal_ctx.backend = _mock_backend(food_truck_capable=False)
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        with pytest.raises(RuntimeError, match="food_truck_capable"):
            await executor.dispatch_food_truck(
                "some prompt",
                str(tmp_path),
                completion_marker="DONE",
            )

    @pytest.mark.anyio
    async def test_dispatch_food_truck_allows_food_truck_capable_true(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.execution.conftest import _mock_backend
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
        minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
        minimal_ctx.backend = _mock_backend(food_truck_capable=True)

        executor = DefaultHeadlessExecutor(minimal_ctx)
        result = await executor.dispatch_food_truck(
            "some prompt",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
        )
        assert result is not None
        assert len(runner.call_args_list) >= 1


def test_default_executor_satisfies_protocol_with_dispatch(minimal_ctx) -> None:
    """DefaultHeadlessExecutor satisfies HeadlessExecutor protocol with dispatch_food_truck."""
    from autoskillit.core import HeadlessExecutor
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    executor = DefaultHeadlessExecutor(minimal_ctx)
    assert isinstance(executor, HeadlessExecutor)


def test_build_interactive_cmd_no_headless_hardening(monkeypatch: pytest.MonkeyPatch) -> None:
    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)

    spec = ClaudeCodeBackend().build_interactive_cmd()
    assert spec.env.get("TERM") != "dumb"
    assert "NO_COLOR" not in spec.env


@pytest.mark.anyio
async def test_headless_executor_forwards_network_access(
    minimal_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-A11: DefaultHeadlessExecutor.run(network_access=True) forwards to run_headless_core."""
    from unittest.mock import AsyncMock, patch

    from autoskillit.core import SkillResult
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    captured_kwargs: dict = {}

    async def fake_run_headless_core(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return SkillResult.crashed(exception=RuntimeError("test"), skill_command="/test-skill")

    with patch(
        "autoskillit.execution.headless.run_headless_core",
        new=AsyncMock(side_effect=fake_run_headless_core),
    ):
        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.run("/test-skill", cwd="", network_access=True)

    assert captured_kwargs.get("network_access") is True, (
        "DefaultHeadlessExecutor.run(network_access=True) must forward network_access=True "
        f"to run_headless_core, got: {captured_kwargs.get('network_access')!r}"
    )
