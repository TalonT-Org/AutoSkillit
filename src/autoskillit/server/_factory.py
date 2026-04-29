"""Composition Root: make_context() is the only location that legally instantiates
all 22 service contracts simultaneously.

server/ is L3 — the only layer permitted to import from both L1 (pipeline/)
and L2 (recipe/, migration/) at the same time. This module is the canonical
factory for wiring a fully-populated ToolContext, replacing the ad-hoc
construction scattered across callers.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    MARKETPLACE_PREFIX,
    DirectInstall,
    FleetLock,
    MarketplaceInstall,
    PluginSource,
    SubprocessRunner,
    WriteBehaviorSpec,
    detect_autoskillit_mcp_prefix,
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
    DefaultGitHubApiLog,
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


def _default_plugin_dir() -> Path:
    """Resolve the autoskillit package root."""
    return pkg_root()


def _resolve_marketplace_cache_path() -> Path:
    """Read the installPath for autoskillit from installed_plugins.json."""
    from autoskillit.core import _get_autoskillit_install_path

    return _get_autoskillit_install_path()


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
    """Detect if autoskillit is marketplace-installed via installed_plugins.json."""
    return detect_autoskillit_mcp_prefix() == MARKETPLACE_PREFIX


def make_context(
    config: AutomationConfig,
    *,
    runner: SubprocessRunner | None = _UNSET,
    plugin_dir: str | None = _UNSET,
    plugin_source: PluginSource = _UNSET,
    fleet_lock: FleetLock | None = None,
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
        plugin_dir: Absolute path to the autoskillit plugin directory for a
                    direct install. When omitted (sentinel), auto-detects via
                    _check_plugin_installed(). When plugin_source is also provided,
                    plugin_source takes precedence.
        plugin_source: PluginSource override. When supplied, used directly
                       without detection. For tests and CLI that construct
                       install mode explicitly.
        fleet_lock: FleetLock implementation to inject. Defaults to
                        FleetSemaphore(max_concurrent_dispatches) when None. Pass a
                        custom implementation in tests to substitute without monkey-patching.

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

    resolved_plugin_source: PluginSource
    if plugin_source is not _UNSET:
        resolved_plugin_source = plugin_source  # type: ignore[assignment]
    elif plugin_dir is not _UNSET and isinstance(plugin_dir, (str, Path)):
        resolved_plugin_source = DirectInstall(plugin_dir=Path(plugin_dir))
    elif _check_plugin_installed():
        try:
            resolved_plugin_source = MarketplaceInstall(
                cache_path=_resolve_marketplace_cache_path()
            )
        except (KeyError, ValueError) as exc:
            logger.warning(
                "marketplace install path unavailable (%s) — falling back to direct install",
                exc,
            )
            resolved_plugin_source = DirectInstall(plugin_dir=_default_plugin_dir())
    else:
        resolved_plugin_source = DirectInstall(plugin_dir=_default_plugin_dir())
    plugin_source = resolved_plugin_source
    gate = DefaultGateState(enabled=False)

    env_project_dir = os.environ.get("AUTOSKILLIT_PROJECT_DIR", "")
    project_dir = Path(env_project_dir) if env_project_dir else Path.cwd()
    temp_dir = resolve_temp_dir(project_dir, config.workspace.temp_dir)
    temp_dir_relpath = temp_dir_display_str(config.workspace.temp_dir)

    provider = SkillsDirectoryProvider(temp_dir_relpath=temp_dir_relpath)
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(provider, ephemeral_root)

    from autoskillit.fleet import (  # lazy: avoids fleet init on server import
        FleetSemaphore,
        build_protected_campaign_ids,
    )

    audit = DefaultAuditLog()
    github_api_log = DefaultGitHubApiLog()
    ctx = ToolContext(
        config=config,
        audit=audit,
        background=DefaultBackgroundSupervisor(audit=audit),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=gate,
        plugin_source=plugin_source,
        runner=runner,
        temp_dir=temp_dir,
        project_dir=project_dir,
        tester=DefaultTestRunner(config=config, runner=runner) if runner is not None else None,
        recipes=DefaultRecipeRepository(),
        db_reader=DefaultDatabaseReader(),
        workspace_mgr=DefaultWorkspaceManager(),
        clone_mgr=DefaultCloneManager(),
        github_client=DefaultGitHubFetcher(token=token_factory, tracker=github_api_log),
        ci_watcher=DefaultCIWatcher(token=token_factory, tracker=github_api_log),
        merge_queue_watcher=DefaultMergeQueueWatcher(token=token_factory, tracker=github_api_log),
        github_api_log=github_api_log,
        session_skill_manager=session_mgr,
        skill_resolver=provider.resolver,
        quota_refresh_task=None,
        fleet_lock=(
            fleet_lock
            if fleet_lock is not None
            else FleetSemaphore(max_concurrent=config.fleet.max_concurrent_dispatches)
        ),
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

    def _resolve_read_only(skill_command: str) -> bool:
        name = resolve_skill_name(skill_command)
        if not name:
            return False
        contract = get_skill_contract(name, load_bundled_manifest())
        return contract.read_only if contract else False

    ctx.output_pattern_resolver = _resolve_output_patterns
    ctx.write_expected_resolver = _resolve_write_behavior
    ctx.read_only_resolver = _resolve_read_only
    ctx.token_factory = token_factory
    ctx.build_protected_campaign_ids = build_protected_campaign_ids
    ctx.executor = DefaultHeadlessExecutor(ctx)
    ctx.migrations = DefaultMigrationService(
        default_migration_engine(), run_headless=ctx.executor.run
    )
    return ctx
