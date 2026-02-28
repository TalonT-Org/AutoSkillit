"""Composition Root: make_context() is the only location that legally instantiates
all 10 service contracts simultaneously.

server/ is L3 — the only layer permitted to import from both L1 (pipeline/)
and L2 (recipe/, migration/) at the same time. This module is the canonical
factory for wiring a fully-populated ToolContext, replacing the ad-hoc
construction scattered across callers.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.config import AutomationConfig
from autoskillit.core.types import SubprocessRunner
from autoskillit.execution.db import DefaultDatabaseReader
from autoskillit.execution.headless import DefaultHeadlessExecutor
from autoskillit.execution.testing import DefaultTestRunner
from autoskillit.migration.engine import DefaultMigrationService, default_migration_engine
from autoskillit.pipeline.audit import AuditLog
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import GateState
from autoskillit.pipeline.tokens import TokenLog
from autoskillit.recipe.io import DefaultRecipeRepository
from autoskillit.workspace.cleanup import DefaultWorkspaceManager


def _default_plugin_dir() -> str:
    """Resolve the autoskillit package root (parent of server/)."""
    return str(Path(__file__).parent.parent)


def make_context(
    config: AutomationConfig,
    *,
    runner: SubprocessRunner | None = None,
    plugin_dir: str | None = None,
) -> ToolContext:
    """Create a fully-wired ToolContext with all 10 service fields populated.

    This is the Composition Root — the only location that should instantiate
    all concrete service implementations simultaneously. Uses a two-step
    construction pattern for DefaultHeadlessExecutor: the context is created
    first (with executor=None), then the executor is constructed with the
    context reference, then assigned back.

    Args:
        config: The loaded AutomationConfig (use load_config() to obtain it).
        runner: Subprocess runner implementation. Defaults to None (tests use
                MockSubprocessRunner; production sets RealSubprocessRunner).
                When None, tester is left as None because DefaultTestRunner
                requires a non-None runner.
        plugin_dir: Absolute path to the autoskillit plugin directory. Defaults
                    to the autoskillit package directory (parent of server/).

    Returns:
        ToolContext with gate starting closed (enabled=False). Call
        gate.enable() (via the open_kitchen prompt) to activate gated tools.
        All service fields are populated except tester when runner is None.
    """
    resolved_dir = plugin_dir if plugin_dir is not None else _default_plugin_dir()
    ctx = ToolContext(
        config=config,
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(enabled=False),
        plugin_dir=resolved_dir,
        runner=runner,
        tester=DefaultTestRunner(config=config, runner=runner) if runner is not None else None,
        recipes=DefaultRecipeRepository(),
        migrations=DefaultMigrationService(default_migration_engine()),
        db_reader=DefaultDatabaseReader(),
        workspace_mgr=DefaultWorkspaceManager(),
    )
    ctx.executor = DefaultHeadlessExecutor(ctx)
    return ctx
