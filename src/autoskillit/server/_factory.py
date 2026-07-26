"""Composition Root: make_context() is the only location that legally instantiates
all 25 service contracts simultaneously.

server/ is IL-3 — the only layer permitted to import from both IL-1 (pipeline/)
and IL-2 (recipe/, migration/) at the same time. This module is the canonical
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
    ContextAdmissionStoreAuthority,
    DirectInstall,
    FleetLock,
    InstalledRecipeExecution,
    PluginSource,
    RecipeExecutionSnapshot,
    SkillExecutionRole,
    SubprocessRunner,
    WriteBehaviorSpec,
    get_logger,
    is_feature_enabled,
    resolve_project_dir,
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
    DefaultSkillSessionContractStore,
    DefaultSubprocessRunner,
    DefaultTestRunner,
    RecordingSubprocessRunner,
    build_replay_runner,
    get_backend,
)
from autoskillit.fleet import FleetSemaphore, build_protected_campaign_ids
from autoskillit.migration import DefaultMigrationService, default_migration_engine
from autoskillit.pipeline import (
    DefaultAuditLog,
    DefaultBackgroundSupervisor,
    DefaultContextAdmissionLedger,
    DefaultGateState,
    DefaultGitHubApiLog,
    DefaultTimingLog,
    DefaultTokenLog,
    ToolContext,
)
from autoskillit.recipe import (
    DefaultRecipeRepository,
    SkillContract,
    get_skill_contract,
    load_bundled_manifest,
    resolve_input_specs,
    resolve_skill_name,
)
from autoskillit.server._recipe_execution import (
    DefaultAuditCycleHeadStore,
    DefaultInputPreflightResolver,
)
from autoskillit.workspace import (
    DefaultCloneManager,
    DefaultSessionSkillManager,
    DefaultWorkspaceManager,
    SkillsDirectoryProvider,
    project_default_plugin_source,
    project_direct_install,
    resolve_ephemeral_root,
    resolve_persistent_session_root,
    validate_skill_tier_roles,
)

logger = get_logger(__name__)


def make_recipe_execution(
    *,
    snapshot: RecipeExecutionSnapshot,
    allowed_root: Path,
) -> InstalledRecipeExecution:
    """Build one execution generation from server-owned protocol implementations."""
    head_store = DefaultAuditCycleHeadStore()
    return InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=head_store,
        input_preflight_resolver=DefaultInputPreflightResolver(
            allowed_root=allowed_root,
            head_store=head_store,
        ),
    )


# Optional-override: _UNSET means "not provided, use factory-computed default" (None is valid).
_UNSET: Any = object()


class _LazyTokenFactory:
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


def make_context(
    config: AutomationConfig,
    *,
    runner: SubprocessRunner | None = _UNSET,
    plugin_dir: str | None = _UNSET,
    plugin_source: PluginSource = _UNSET,
    fleet_lock: FleetLock | None = None,
    project_dir: Path | None = None,
) -> ToolContext:
    """Create a fully-wired ToolContext with all 25 service fields populated.

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
        plugin_dir: Test-injection override for the projection *source* — project
                    from this root instead of pkg_root(). Still projected, never
                    handed to a session raw. When plugin_source is also provided,
                    plugin_source wins.
        plugin_source: Test-injection override for the fully-resolved PluginSource.
                       When supplied, used verbatim with no projection.
        fleet_lock: FleetLock implementation to inject. Defaults to
                        FleetSemaphore(max_concurrent_dispatches) when None. Pass a
                        custom implementation in tests to substitute without monkey-patching.
        project_dir: Explicit project root path. When supplied, used directly.
                     When None, resolve_project_dir() is called (git toplevel → cwd) —
                     the same helper `autoskillit cook` uses.
                     Pass tmp_path in tests to avoid subprocess calls and ensure isolation.

    Returns:
        ToolContext with gate starting closed (enabled=False) in all contexts.
        Tag-based visibility (mcp.enable({'headless'}) or open_kitchen) controls
        tool reveal — the gate itself is never pre-enabled at startup.
        All service fields are populated, including backend (resolved from
        config.agent_backend via the BACKEND_REGISTRY). When runner=None is
        passed explicitly, tester is left as None.
    """
    if runner is _UNSET:
        runner = DefaultSubprocessRunner()

    _codex_feature_enabled = is_feature_enabled(
        "codex_backend", config.features, experimental_enabled=config.experimental_enabled
    )
    if _codex_feature_enabled and config.agent_backend.backend != "codex":
        logger.warning(
            "codex_backend_flag_ignored",
            reason="config.agent_backend.backend is not 'codex'",
            configured_backend=config.agent_backend.backend,
        )
    backend = get_backend(config.agent_backend.backend)

    if runner is not None and os.environ.get(REPLAY_SCENARIO_ENV):
        if not backend.capabilities.replay_capable:
            logger.warning(
                "REPLAY_SCENARIO is set but backend %r is not replay-capable "
                "— skipping replay runner construction",
                config.agent_backend.backend,
            )
        else:
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
        if not backend.capabilities.record_capable:
            logger.warning(
                "RECORD_SCENARIO is set but backend %r is not record-capable "
                "— skipping record runner construction",
                config.agent_backend.backend,
            )
        else:
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
                        recorder = make_scenario_recorder(
                            output_dir=scenario_dir, recipe_name=recipe_name
                        )
                        runner = RecordingSubprocessRunner(
                            recorder=recorder,
                            inner=runner,
                            scenario_dir=Path(scenario_dir),
                            capabilities=backend.capabilities,
                        )

    # Lazy token resolution: config → GITHUB_TOKEN env var → gh CLI → None.
    # The _gh_cli_token() subprocess (up to 5s) is deferred until the first
    # gated tool actually needs a GitHub token, keeping the MCP server startup
    # path free of subprocess calls (REQ-STARTUP-001).
    token_factory = _LazyTokenFactory(
        lambda: config.github.token or os.environ.get("GITHUB_TOKEN") or _gh_cli_token()
    )

    gate = DefaultGateState(enabled=False)

    project_dir = project_dir if project_dir is not None else resolve_project_dir()
    temp_dir = resolve_temp_dir(project_dir, config.workspace.temp_dir)
    temp_dir_relpath = temp_dir_display_str(config.workspace.temp_dir)

    provider = SkillsDirectoryProvider(
        temp_dir_relpath=temp_dir_relpath,
        default_base_branch=config.branching.default_base_branch,
    )
    skill_visibility = config.skill_visibility_spec()
    validate_skill_tier_roles(skill_visibility, provider.resolver, project_dir)
    session_catalog = provider.resolver.list_effective(
        project_dir,
        SkillExecutionRole.SESSION,
        visibility=skill_visibility,
    )
    # Single resolution authority, shared with `autoskillit cook`: the plugin
    # source is the projected running package. The two overrides exist only for
    # test injection — nothing in production supplies them, and neither can name
    # a path read out of installed_plugins.json.
    resolved_plugin_source: PluginSource
    if plugin_source is not _UNSET:
        resolved_plugin_source = plugin_source  # type: ignore[assignment]
    elif plugin_dir is not _UNSET and isinstance(plugin_dir, (str, Path)):
        resolved_plugin_source = project_direct_install(
            DirectInstall(plugin_dir=Path(plugin_dir)),
            cwd=project_dir,
            backend=backend,
            default_base_branch=config.branching.default_base_branch,
            skill_catalog=session_catalog,
        )
    else:
        resolved_plugin_source = project_default_plugin_source(
            cwd=project_dir,
            backend=backend,
            default_base_branch=config.branching.default_base_branch,
            skill_catalog=session_catalog,
        )
    ephemeral_root = resolve_ephemeral_root()
    persistent_root = resolve_persistent_session_root(temp_dir, backend)
    session_mgr = DefaultSessionSkillManager(
        provider,
        ephemeral_root,
        persistent_root=persistent_root,
    )

    audit = DefaultAuditLog()
    github_api_log = DefaultGitHubApiLog()
    context_admission_ledger = DefaultContextAdmissionLedger(
        ContextAdmissionStoreAuthority(
            database_path=(temp_dir / "context-admission" / "ledger.sqlite3").resolve(),
            expected_owner_id=os.getuid(),
        )
    )
    ctx = ToolContext(
        config=config,
        audit=audit,
        background=DefaultBackgroundSupervisor(audit=audit),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=gate,
        plugin_source=resolved_plugin_source,
        runner=runner,
        backend=backend,
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
        skill_session_contract_store=DefaultSkillSessionContractStore(),
        context_admission_ledger=context_admission_ledger,
        ephemeral_root=ephemeral_root,
        quota_refresh_task=None,
        session_serve_overrides=None,
        fleet_lock=(
            fleet_lock
            if fleet_lock is not None
            else FleetSemaphore(
                max_concurrent=config.fleet.max_concurrent_dispatches,
                timeout=config.fleet.acquire_timeout_sec,
            )
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

    def _resolve_completion_required(skill_command: str) -> bool:
        name = resolve_skill_name(skill_command)
        if not name:
            return False
        contract = get_skill_contract(name, load_bundled_manifest())
        return contract.completion_required if contract else False

    def _resolve_skill_contract(skill_command: str) -> SkillContract | None:
        name = resolve_skill_name(skill_command)
        if not name:
            return None
        return get_skill_contract(name, load_bundled_manifest())

    ctx.output_pattern_resolver = _resolve_output_patterns
    ctx.write_expected_resolver = _resolve_write_behavior
    ctx.read_only_resolver = _resolve_read_only
    ctx.completion_required_resolver = _resolve_completion_required
    ctx.skill_contract_resolver = _resolve_skill_contract
    ctx.input_contract_resolver = resolve_input_specs
    ctx.recipe_execution_factory = make_recipe_execution
    ctx.token_factory = token_factory
    ctx.build_protected_campaign_ids = build_protected_campaign_ids
    ctx.executor = DefaultHeadlessExecutor(ctx)
    ctx.migrations = DefaultMigrationService(
        default_migration_engine(), run_headless=ctx.executor.run
    )
    return ctx
