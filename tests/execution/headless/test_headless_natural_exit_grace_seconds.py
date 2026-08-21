"""Regression coverage for issue #4686.

Threads cfg.run_skill.natural_exit_grace_seconds to the runner on both
the leaf ``run_skill`` and fleet ``dispatch_food_truck`` paths.
"""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig, RunSkillConfig
from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result
from tests.fakes import MockSubprocessRunner
from tests.server.conftest import _SUCCESS_JSON

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _configure_run_skill(
    ctx,
    *,
    natural_exit_grace_seconds: float,
    exit_after_stop_delay_ms: int,
) -> None:
    """Set ctx.config.run_skill to a coherent (grace, delay) pair for testing."""
    cfg = AutomationConfig()
    cfg.run_skill = RunSkillConfig(
        natural_exit_grace_seconds=natural_exit_grace_seconds,
        exit_after_stop_delay_ms=exit_after_stop_delay_ms,
    )
    cfg.safety.require_dry_walkthrough = False
    ctx.config = cfg


@pytest.mark.anyio
async def test_leaf_run_skill_threads_natural_exit_grace_seconds(tool_ctx_kitchen_open) -> None:
    """Leaf run_skill path: cfg.run_skill.natural_exit_grace_seconds reaches the runner."""
    _configure_run_skill(
        tool_ctx_kitchen_open,
        natural_exit_grace_seconds=7.5,
        exit_after_stop_delay_ms=2000,
    )
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
    tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
    await run_skill("/investigate something", "/tmp")
    _, _, _, kwargs = tool_ctx_kitchen_open.runner.call_args_list[-1]
    assert kwargs["natural_exit_grace_seconds"] == 7.5


@pytest.mark.anyio
async def test_food_truck_dispatch_threads_natural_exit_grace_seconds(
    minimal_ctx, tmp_path
) -> None:
    """Fleet dispatch_food_truck path: the configured value reaches the runner."""
    from autoskillit.core.types import SubprocessResult, TerminationReason
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.execution.headless import DefaultHeadlessExecutor
    from tests.execution.test_headless_dispatch import _StaticPluginAuthority

    _configure_run_skill(
        minimal_ctx,
        natural_exit_grace_seconds=8.0,
        exit_after_stop_delay_ms=2000,
    )

    runner = MockSubprocessRunner()
    runner.set_default(
        SubprocessResult(
            returncode=0,
            stdout=_SUCCESS_JSON,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=55555,
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
        plugin_authority=minimal_ctx.plugin_authority,
    )

    assert runner.call_args_list, "runner was never called"
    matching_calls = [
        entry
        for entry in runner.call_args_list
        if entry[3].get("natural_exit_grace_seconds") == 8.0
    ]
    assert matching_calls, "no runner call threaded natural_exit_grace_seconds=8.0"
    _, _, _, kwargs = matching_calls[0]
    assert kwargs["natural_exit_grace_seconds"] == 8.0
