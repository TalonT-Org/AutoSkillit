"""Composition Root: make_context() is the only location that legally instantiates
all 22 service contracts simultaneously.

server/ is L3 — the only layer permitted to import from both L1 (pipeline/)
and L2 (recipe/, migration/) at the same time. This module is the canonical
factory for wiring a fully-populated ToolContext, replacing the ad-hoc
construction scattered across callers.
"""

from __future__ import annotations

import os
from typing import Any

from autoskillit.config import AutomationConfig
from autoskillit.core import SubprocessRunner, WriteBehaviorSpec, get_logger, pkg_root
from autoskillit.execution import (
    DefaultCIWatcher,
    DefaultDatabaseReader,
    DefaultGitHubFetcher,
    DefaultHeadlessExecutor,
    DefaultMergeQueueWatcher,
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
from autoskillit.recipe import (
    DefaultRecipeRepository,
    get_skill_contract,
    load_bundled_manifest,
    resolve_skill_name,
)
from autoskillit.workspace import (
    DefaultCloneManager,
    DefaultSessionSkillManager,
    DefaultWorkspaceManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)

logger = get_logger(__name__)

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
    """Create a fully-wired ToolContext with all 22 service fields populated.

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
        ToolContext with gate starting closed (enabled=False) in all contexts.
        Tag-based visibility (mcp.enable({'headless'}) or open_kitchen) controls
        tool reveal — the gate itself is never pre-enabled at startup.
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

    provider = SkillsDirectoryProvider()
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(provider, ephemeral_root)

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
        ci_watcher=DefaultCIWatcher(token=github_token),
        merge_queue_watcher=DefaultMergeQueueWatcher(token=github_token),
        session_skill_manager=session_mgr,
        skill_resolver=provider.resolver,
    )

    def _resolve_output_patterns(skill_command: str) -> list[str]:
        name = resolve_skill_name(skill_command)
        if not name:
            return []
        contract = get_skill_contract(name, load_bundled_manifest())
        if not contract:
            return []
        return contract.expected_output_patterns

    def _resolve_write_behavior(skill_command: str) -> WriteBehaviorSpec:
        name = resolve_skill_name(skill_command)
        if not name:
            return WriteBehaviorSpec()
        contract = get_skill_contract(name, load_bundled_manifest())
        if contract is None or contract.write_behavior is None:
            return WriteBehaviorSpec()
        return WriteBehaviorSpec(
            mode=contract.write_behavior,
            expected_when=tuple(contract.write_expected_when),
        )

    ctx.output_pattern_resolver = _resolve_output_patterns
    ctx.write_expected_resolver = _resolve_write_behavior
    ctx.executor = DefaultHeadlessExecutor(ctx)
    ctx.migrations = DefaultMigrationService(
        default_migration_engine(), run_headless=ctx.executor.run
    )
    return ctx
