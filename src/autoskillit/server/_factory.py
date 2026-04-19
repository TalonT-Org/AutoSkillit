"""Composition Root: make_context() is the only location that legally instantiates
all 22 service contracts simultaneously.

server/ is L3 — the only layer permitted to import from both L1 (pipeline/)
and L2 (recipe/, migration/) at the same time. This module is the canonical
factory for wiring a fully-populated ToolContext, replacing the ad-hoc
construction scattered across callers.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    FranchiseLock,
    SubprocessRunner,
    WriteBehaviorSpec,
    get_logger,
    pkg_root,
    resolve_temp_dir,
    temp_dir_display_str,
)
from autoskillit.execution import (
    RECORD_SCENARIO_DIR_ENV,
    RECORD_SCENARIO_ENV,
    RECORD_SCENARIO_RECIPE_ENV,
    REPLAY_SCENARIO_DIR_ENV,
    REPLAY_SCENARIO_ENV,
    DefaultCIWatcher,
    DefaultDatabaseReader,
    DefaultGitHubFetcher,
    DefaultHeadlessExecutor,
    DefaultMergeQueueWatcher,
    DefaultTestRunner,
    build_replay_runner,
)
from autoskillit.migration import DefaultMigrationService, default_migration_engine
from autoskillit.pipeline import (
    DefaultAuditLog,
    DefaultBackgroundSupervisor,
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


class TokenFactory:
    """Lazy-resolving, caching token factory.

    Wraps the config -> env -> gh CLI token resolution chain.  Does NOT
    resolve at construction time.  First call resolves and caches the
    result; subsequent calls return the cached value.

    Thread-safe for single-writer scenarios (GIL-safe sentinel + assignment
    pattern; the MCP server is single-threaded asyncio).
    """

    _UNRESOLVED = object()

    def __init__(self, resolver: Callable[[], str | None]) -> None:
        self._resolver = resolver
        self._resolved: str | None = self._UNRESOLVED  # type: ignore[assignment]

    def __call__(self) -> str | None:
        if self._resolved is self._UNRESOLVED:
            self._resolved = self._resolver()
        return self._resolved

    @property
    def is_resolved(self) -> bool:
        return self._resolved is not self._UNRESOLVED


def _default_plugin_dir() -> str:
    """Resolve the autoskillit package root."""
    return str(pkg_root())


def _gh_cli_token() -> str | None:
    """Try to obtain a GitHub token from the ``gh`` CLI.

    Returns the token string on success, ``None`` if ``gh`` is not installed,
    the user is not logged in, or the command fails for any reason.
    Never raises.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        logger.debug("gh auth token unavailable", exc_info=True)
    return None


def _check_plugin_installed() -> bool:
    """Deferred import to avoid server→cli module-level dependency."""
    from autoskillit.cli import _is_plugin_installed  # noqa: PLC0415

    return _is_plugin_installed()


def make_context(
    config: AutomationConfig,
    *,
    runner: SubprocessRunner | None = _UNSET,
    plugin_dir: str | None = _UNSET,
    franchise_lock: FranchiseLock | None = None,
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
        plugin_dir: Absolute path to the autoskillit plugin directory.
                    Pass None explicitly to indicate the plugin is installed
                    (marketplace install; no --plugin-dir needed). When omitted
                    (sentinel), auto-detects via _check_plugin_installed().
        franchise_lock: FranchiseLock implementation to inject. Defaults to
                        asyncio.Lock() when None. Pass a custom implementation
                        in tests to substitute the lock without monkey-patching.

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

    if runner is not None and os.environ.get(REPLAY_SCENARIO_ENV):
        replay_dir = os.environ.get(REPLAY_SCENARIO_DIR_ENV, "")
        if not replay_dir:
            logger.warning(
                "REPLAY_SCENARIO is set but REPLAY_SCENARIO_DIR is empty — skipping replay"
            )
        elif not os.path.isdir(replay_dir):
            logger.warning(
                "REPLAY_SCENARIO_DIR=%r is not an existing directory — skipping replay",
                replay_dir,
            )
        else:
            runner = build_replay_runner(replay_dir)

    elif runner is not None and os.environ.get(RECORD_SCENARIO_ENV):
        scenario_dir = os.environ.get(RECORD_SCENARIO_DIR_ENV, "")
        recipe_name = os.environ.get(RECORD_SCENARIO_RECIPE_ENV, "unknown")
        if scenario_dir:
            if not os.path.isdir(scenario_dir):
                logger.warning(
                    "RECORD_SCENARIO_DIR=%r is not an existing directory — skipping recording",
                    scenario_dir,
                )
            else:
                try:
                    from api_simulator.claude import make_scenario_recorder
                except ImportError:
                    logger.warning(
                        "RECORD_SCENARIO is set but 'api_simulator' is not installed "
                        "— skipping recording"
                    )
                    make_scenario_recorder = None  # type: ignore[assignment]

                if make_scenario_recorder is not None:
                    from autoskillit.execution import RecordingSubprocessRunner

                    recorder = make_scenario_recorder(
                        output_dir=scenario_dir, recipe_name=recipe_name
                    )
                    runner = RecordingSubprocessRunner(recorder=recorder, inner=runner)

    # Lazy token resolution: config → GITHUB_TOKEN env var → gh CLI → None.
    # The _gh_cli_token() subprocess (up to 5s) is deferred until the first
    # gated tool actually needs a GitHub token, keeping the MCP server startup
    # path free of subprocess calls (REQ-STARTUP-001).
    token_factory = TokenFactory(
        lambda: config.github.token or os.environ.get("GITHUB_TOKEN") or _gh_cli_token()
    )

    resolved_dir = (
        plugin_dir
        if plugin_dir is not _UNSET
        else (_default_plugin_dir() if not _check_plugin_installed() else None)
    )
    gate = DefaultGateState(enabled=False)

    env_project_dir = os.environ.get("AUTOSKILLIT_PROJECT_DIR", "")
    project_dir = Path(env_project_dir) if env_project_dir else Path.cwd()
    temp_dir = resolve_temp_dir(project_dir, config.workspace.temp_dir)
    temp_dir_relpath = temp_dir_display_str(config.workspace.temp_dir)

    provider = SkillsDirectoryProvider(temp_dir_relpath=temp_dir_relpath)
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(provider, ephemeral_root)

    audit = DefaultAuditLog()
    ctx = ToolContext(
        config=config,
        audit=audit,
        background=DefaultBackgroundSupervisor(audit=audit),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=gate,
        plugin_dir=resolved_dir,
        runner=runner,
        temp_dir=temp_dir,
        project_dir=project_dir,
        tester=DefaultTestRunner(config=config, runner=runner) if runner is not None else None,
        recipes=DefaultRecipeRepository(),
        db_reader=DefaultDatabaseReader(),
        workspace_mgr=DefaultWorkspaceManager(),
        clone_mgr=DefaultCloneManager(),
        github_client=DefaultGitHubFetcher(token=token_factory),
        ci_watcher=DefaultCIWatcher(token=token_factory),
        merge_queue_watcher=DefaultMergeQueueWatcher(token=token_factory),
        session_skill_manager=session_mgr,
        skill_resolver=provider.resolver,
        quota_refresh_task=None,
        franchise_lock=franchise_lock if franchise_lock is not None else asyncio.Lock(),
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
    ctx.token_factory = token_factory
    ctx.executor = DefaultHeadlessExecutor(ctx)
    ctx.migrations = DefaultMigrationService(
        default_migration_engine(), run_headless=ctx.executor.run
    )
    return ctx
