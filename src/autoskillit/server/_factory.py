"""Composition Root: make_context() is the only location that legally instantiates
all 11 service contracts simultaneously.

server/ is L3 — the only layer permitted to import from both L1 (pipeline/)
and L2 (recipe/, migration/) at the same time. This module is the canonical
factory for wiring a fully-populated ToolContext, replacing the ad-hoc
construction scattered across callers.
"""

from __future__ import annotations

import os
from typing import Any

from autoskillit.config import AutomationConfig
from autoskillit.core import SubprocessRunner, pkg_root
from autoskillit.execution import (
    DefaultDatabaseReader,
    DefaultGitHubFetcher,
    DefaultHeadlessExecutor,
    DefaultTestRunner,
)
from autoskillit.migration import DefaultMigrationService, default_migration_engine
from autoskillit.pipeline import (
    DefaultAuditLog,
    DefaultGateState,
    DefaultTimingLog,
    DefaultTokenLog,
    ToolContext,
)
from autoskillit.recipe import DefaultRecipeRepository
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.workspace import DefaultCloneManager, DefaultWorkspaceManager

# Sentinel: distinguish "caller passed runner=None explicitly" from "not provided"
_UNSET: Any = object()


def _default_plugin_dir() -> str:
    """Resolve the autoskillit package root."""
    return str(pkg_root())


def make_context(
    config: AutomationConfig,
    *,
    runner: SubprocessRunner | None = _UNSET,
    plugin_dir: str | None = None,
) -> ToolContext:
    """Create a fully-wired ToolContext with all 12 service fields populated.

    This is the Composition Root — the only location that should instantiate
    all concrete service implementations simultaneously. Uses a three-step
    construction pattern: the context is created first (with executor and
    migrations as None), then the executor is constructed with the context
    reference and assigned back, then migrations is constructed with the
    executor's run method injected via constructor.

    Args:
        config: The loaded AutomationConfig (use load_config() to obtain it).
        runner: Subprocess runner implementation. Defaults to DefaultSubprocessRunner()
                for production use. Pass runner=None explicitly to disable the
                tester (useful in tests that don't need real subprocess execution).
        plugin_dir: Absolute path to the autoskillit plugin directory. Defaults
                    to the autoskillit package directory (parent of server/).

    Returns:
        ToolContext with gate starting closed (enabled=False). Call
        gate.enable() (via the open_kitchen prompt) to activate gated tools.
        All service fields are populated. When runner=None is passed explicitly,
        tester is left as None.
    """
    if runner is _UNSET:
        from autoskillit.execution import DefaultSubprocessRunner

        runner = DefaultSubprocessRunner()

    # Resolve token: config → GITHUB_TOKEN env var → None (unauthenticated)
    github_token = config.github.token or os.environ.get("GITHUB_TOKEN")

    resolved_dir = plugin_dir if plugin_dir is not None else _default_plugin_dir()
    gate = DefaultGateState(enabled=False)
    if os.environ.get("AUTOSKILLIT_KITCHEN_OPEN") == "1":
        gate.enable()
    ctx = ToolContext(
        config=config,
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=gate,
        plugin_dir=resolved_dir,
        runner=runner,
        tester=DefaultTestRunner(config=config, runner=runner) if runner is not None else None,
        recipes=DefaultRecipeRepository(),
        db_reader=DefaultDatabaseReader(),
        workspace_mgr=DefaultWorkspaceManager(),
        clone_mgr=DefaultCloneManager(),
        github_client=DefaultGitHubFetcher(token=github_token),
    )

    def _resolve_output_patterns(skill_command: str) -> list[str]:
        name = resolve_skill_name(skill_command)
        if not name:
            return []
        contract = get_skill_contract(name, load_bundled_manifest())
        if not contract:
            return []
        return contract.expected_output_patterns

    ctx.output_pattern_resolver = _resolve_output_patterns
    ctx.executor = DefaultHeadlessExecutor(ctx)
    ctx.migrations = DefaultMigrationService(
        default_migration_engine(), run_headless=ctx.executor.run
    )
    return ctx
