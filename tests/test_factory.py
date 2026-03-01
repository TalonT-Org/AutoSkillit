"""Tests for server/_factory.py make_context() composition root."""

from __future__ import annotations

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SkillResult, SubprocessResult, TerminationReason
from autoskillit.execution.db import DefaultDatabaseReader
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.testing import DefaultTestRunner
from autoskillit.migration.engine import DefaultMigrationService
from autoskillit.pipeline.context import ToolContext
from autoskillit.recipe.repository import DefaultRecipeRepository
from autoskillit.server._factory import make_context
from autoskillit.workspace.cleanup import DefaultWorkspaceManager
from tests.conftest import MockSubprocessRunner


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


def test_make_context_returns_toolcontext():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx, ToolContext)


def test_make_context_gate_starts_closed():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.gate.enabled is False


def test_make_context_executor_is_default_headless():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx.executor, DefaultHeadlessExecutor)


def test_make_context_tester_is_default_test_runner():
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert isinstance(ctx.tester, DefaultTestRunner)


def test_make_context_all_service_fields_populated_with_runner():
    """All 10 service contracts are populated when a runner is provided."""
    ctx = make_context(AutomationConfig(), runner=_runner())
    assert ctx.executor is not None
    assert ctx.tester is not None
    assert ctx.recipes is not None
    assert ctx.migrations is not None
    assert ctx.db_reader is not None
    assert ctx.workspace_mgr is not None
    assert isinstance(ctx.recipes, DefaultRecipeRepository)
    assert isinstance(ctx.migrations, DefaultMigrationService)
    assert isinstance(ctx.db_reader, DefaultDatabaseReader)
    assert isinstance(ctx.workspace_mgr, DefaultWorkspaceManager)


def test_make_context_tester_none_when_no_runner():
    """When runner=None, DefaultTestRunner cannot be constructed; tester is None."""
    ctx = make_context(AutomationConfig(), runner=None)
    assert ctx.tester is None


def test_make_context_protocol_substitution():
    """Any object satisfying HeadlessExecutor protocol can replace ctx.executor."""
    from autoskillit.core.types import HeadlessExecutor

    class FakeExecutor:
        async def run(
            self,
            skill_command: str,
            cwd: str,
            *,
            model: str = "",
            step_name: str = "",
            add_dir: str = "",
        ) -> SkillResult:
            return SkillResult(
                success=True,
                result="",
                session_id="",
                subtype="",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason="none",
                stderr="",
                token_usage=None,
            )

    ctx = make_context(AutomationConfig(), runner=_runner())
    ctx.executor = FakeExecutor()
    assert isinstance(ctx.executor, HeadlessExecutor)
