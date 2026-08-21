"""Tests that RunSkillConfig.natural_exit_grace_seconds reaches run_managed_async.

Closes the issue #4686 gap. The config field was previously inert: declared and
validated, but dropped at every hop in the call chain. The wiring added by
this issue threads the value from the leaf run_skill call site through
``_execute_claude_headless`` → ``_run_headless_attempt`` →
``DefaultSubprocessRunner.__call__`` → ``run_managed_async`` →
``execute_termination_action(..., grace_seconds=...)``.

The leaf path is exercised by the ``run_skill`` tool. A ``MockSubprocessRunner``
(from ``tests.fakes``) records every kwarg it receives; these tests assert the
configured value lands in the captured kwargs dict instead of the runner's
hardcoded 3.0 s default.
"""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig, RunSkillConfig
from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result
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
async def test_default_value_passes_through_unchanged(tool_ctx_kitchen_open) -> None:
    """The 3.0 s default is preserved end-to-end when no override is set."""
    cfg = AutomationConfig()
    cfg.safety.require_dry_walkthrough = False
    tool_ctx_kitchen_open.config = cfg
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
    tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
    await run_skill("/investigate something", "/tmp")
    _, _, _, kwargs = tool_ctx_kitchen_open.runner.call_args_list[-1]
    # The default 3.0 from RunSkillConfig flows through unchanged.
    assert kwargs["natural_exit_grace_seconds"] == 3.0
