"""ToolContext: explicit dependency container for server tool implementations.

pipeline/ module — the only pipeline sub-module that imports from config/.
Replaces two mutable module-level singletons in server.py:
  _config, _tools_enabled
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    ActiveRecipeRuntimeSnapshot,
    AuditLog,
    BackgroundSupervisor,
    CampaignProtector,
    CIRunScope,
    CIWatcher,
    CloneManager,
    CodingAgentBackend,
    CompletionRequiredResolver,
    DatabaseReader,
    FleetLock,
    GateState,
    GitHubApiLog,
    GitHubFetcher,
    HeadlessExecutor,
    InputContractResolver,
    McpResponseLog,
    MergeQueueWatcher,
    MigrationService,
    OutputPatternResolver,
    PluginSource,
    QuotaRefreshTask,
    ReadOnlyResolver,
    RecipeRepository,
    ServeOverridesSnapshot,
    SessionSkillManager,
    SkillResolver,
    SubprocessRunner,
    TestRunner,
    TimingLog,
    TokenFactory,
    TokenLog,
    WorkspaceManager,
    WriteExpectedResolver,
    current_order_id,
    current_step_name,
)
from autoskillit.pipeline.background import DefaultBackgroundSupervisor
from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog

__all__ = ["ToolContext", "current_step_name", "current_order_id"]

# Must-supply-or-raise: fields defaulting to _MISSING are required by __post_init__.
_MISSING: Any = object()


@dataclass
class ToolContext:
    """Single dependency container threaded through all MCP tool implementations.

    Constructed once in cli.py serve() via server._factory.make_context() and
    injected into server.py via server._initialize(ctx). Tests construct isolated
    instances per-test to avoid global state leakage.

    Fields
    ------
    config:               AutomationConfig loaded from .autoskillit/config.yaml
    audit:                AuditLog — records pipeline failures
    token_log:            TokenLog — per-step token tracking
    timing_log:           TimingLog — per-step wall-clock duration tracking
    response_log:         McpResponseLog — per-tool MCP response size tracking
    gate:                 GateState — enables/disables gated tools
    plugin_source:        PluginSource — DirectInstall (dev/editable) or MarketplaceInstall.
                          Encodes how autoskillit is loaded into Claude Code sessions.
    runner:               SubprocessRunner implementation (DefaultSubprocessRunner in production,
                          MockSubprocessRunner in tests)
    backend:              CodingAgentBackend — the coding agent backend resolved from
                          config.agent_backend. Provides command building, stream/result
                          parsing, env policy, and session location. None in test
                          ToolContext instances unless explicitly provided.
    executor:             HeadlessExecutor — runs headless Claude Code sessions
    tester:               TestRunner — runs the project test suite
    recipes:              RecipeRepository — loads and lists pipeline recipes
    migrations:           MigrationService — applies versioned migration notes to recipes
    db_reader:            DatabaseReader — executes read-only SQLite queries
    workspace_mgr:        WorkspaceManager — manages workspace directory teardown
    clone_mgr:            CloneManager — clone-based pipeline run isolation
    github_client:        GitHubFetcher — fetches GitHub issue content
    ci_watcher:           CIWatcher — watches GitHub Actions CI runs
    merge_queue_watcher:  MergeQueueWatcher — polls GitHub merge queue for a PR
    github_api_log:       GitHubApiLog — session-scoped GitHub API request accumulator.
    background:           BackgroundSupervisor — supervised async background task execution.
                          Auto-initialized to DefaultBackgroundSupervisor when None.
    output_pattern_resolver: OutputPatternResolver — resolves expected output patterns from
                          a skill command.
    write_expected_resolver: WriteExpectedResolver — resolves write-expectation metadata
                          from skill contracts.
    read_only_resolver:   ReadOnlyResolver — resolves whether a skill is read-only from
                          skill contracts.
    input_contract_resolver: InputContractResolver — resolves input contract specs from
                          skill contracts.
    completion_required_resolver: CompletionRequiredResolver — resolves whether a skill
                          requires the completion marker.
    quota_refresh_task:   QuotaRefreshTask — cancellable handle for the kitchen-scoped
                          quota refresh background task.
    fleet_lock:           FleetLock — semaphore-style guard for concurrent fleet dispatch.
    build_protected_campaign_ids: CampaignProtector — resolves campaign IDs exempt from
                          log retention purge.
    session_skill_manager: SessionSkillManager — manages per-session ephemeral skill dirs
    skill_resolver:       SkillResolver — resolves skill names to source tier
    kitchen_id:           UUID string assigned when open_kitchen fires; scopes token telemetry
                          to the current kitchen session lifetime.
    active_recipe_snapshot: ActiveRecipeRuntimeSnapshot | None — atomic, immutable runtime
                          view of the active recipe. None when the kitchen is closed.
                          Reads must go through this field; the legacy
                          ``active_recipe_*`` attributes are derived from it for
                          backward compatibility with existing tool handlers.
                          Install or clear via :meth:`set_active_recipe_snapshot`.
    active_recipe_packs:  frozenset[str] | None — pack names declared by the loaded recipe
                          (frozenset() when kitchen open but no recipe loaded; None when closed).
                          Derived from ``active_recipe_snapshot``; mutable direct
                          assignment is deprecated in favor of
                          :meth:`set_active_recipe_snapshot`.
    active_recipe_features: frozenset[str] | None — feature names declared by the loaded recipe
                          (frozenset() when kitchen open but no recipe loaded; None when closed).
                          Derived from ``active_recipe_snapshot``.
    active_recipe_steps:  dict[str, Any] | None — step definitions cached from the loaded recipe
                          ({} when kitchen open but no recipe loaded; None when closed).
                          Derived from ``active_recipe_snapshot``.
    active_recipe_ingredients: frozenset[str] | None — ingredient keys declared by the loaded
                          recipe (frozenset() when kitchen open but no recipe loaded; None when
                          closed). Derived from ``active_recipe_snapshot``.
    temp_dir:             Resolved temp directory for this project. Sentinel-guarded: raises
                          TypeError if not supplied explicitly. Use make_context() or pass
                          temp_dir=<path>.
    token_factory:        Optional callable that resolves a GitHub token via the
                          config → GITHUB_TOKEN env → gh CLI fallback chain.
                          Set by make_context(); None in test ToolContext instances
                          unless explicitly provided.
    project_dir:          Resolved project root directory. Sentinel-guarded: raises TypeError
                          if not supplied explicitly. Use make_context() or pass
                          project_dir=<path>.
    """

    config: AutomationConfig
    audit: AuditLog
    token_log: TokenLog
    timing_log: TimingLog
    gate: GateState
    plugin_source: PluginSource
    runner: SubprocessRunner | None
    temp_dir: Path = field(default=_MISSING)
    project_dir: Path = field(default=_MISSING)
    response_log: McpResponseLog = field(default_factory=DefaultMcpResponseLog)
    executor: HeadlessExecutor | None = field(default=None)
    tester: TestRunner | None = field(default=None)
    recipes: RecipeRepository | None = field(default=None)
    migrations: MigrationService | None = field(default=None)
    db_reader: DatabaseReader | None = field(default=None)
    workspace_mgr: WorkspaceManager | None = field(default=None)
    clone_mgr: CloneManager | None = field(default=None)
    github_client: GitHubFetcher | None = field(default=None)
    ci_watcher: CIWatcher | None = field(default=None)
    merge_queue_watcher: MergeQueueWatcher | None = field(default=None)
    github_api_log: GitHubApiLog | None = field(default=None)
    background: BackgroundSupervisor | None = field(default=None)
    output_pattern_resolver: OutputPatternResolver | None = field(default=None)
    write_expected_resolver: WriteExpectedResolver | None = field(default=None)
    read_only_resolver: ReadOnlyResolver | None = field(default=None)
    input_contract_resolver: InputContractResolver | None = field(default=None)
    completion_required_resolver: CompletionRequiredResolver | None = field(default=None)
    backend: CodingAgentBackend | None = field(default=None)
    session_skill_manager: SessionSkillManager | None = field(default=None)
    skill_resolver: SkillResolver | None = field(default=None)
    recipe_name: str = field(default="")
    recipe_content_hash: str = field(default="")
    recipe_composite_hash: str = field(default="")
    recipe_version: str = field(default="")
    gate_infrastructure_ready: bool = field(default=False)
    kitchen_id: str = field(default="")
    active_recipe_snapshot: ActiveRecipeRuntimeSnapshot | None = field(
        default_factory=lambda: None
    )
    active_recipe_packs: frozenset[str] | None = field(default_factory=lambda: None)
    active_recipe_features: frozenset[str] | None = field(default_factory=lambda: None)
    active_recipe_steps: dict[str, Any] | None = field(default_factory=lambda: None)
    active_recipe_ingredients: frozenset[str] | None = field(default_factory=lambda: None)
    session_serve_overrides: ServeOverridesSnapshot | None = field(default_factory=lambda: None)
    session_serve_defer_unresolved: bool = field(default=False)
    quota_refresh_task: QuotaRefreshTask | None = field(default=None)
    token_factory: TokenFactory | None = field(default=None)
    fleet_lock: FleetLock | None = field(default=None)
    build_protected_campaign_ids: CampaignProtector | None = field(default=None)
    ephemeral_root: Path | None = field(default_factory=lambda: None)

    def __post_init__(self) -> None:
        if self.temp_dir is _MISSING:
            raise TypeError(
                "temp_dir must be supplied explicitly — do not rely on defaults. "
                "Use make_context() or pass temp_dir=<path> directly."
            )
        if self.project_dir is _MISSING:
            raise TypeError(
                "project_dir must be supplied explicitly — do not rely on defaults. "
                "Use make_context() or pass project_dir=<path> directly."
            )
        if self.background is None:
            self.background = DefaultBackgroundSupervisor(audit=self.audit)

    @property
    def default_ci_scope(self) -> CIRunScope:
        """Build the default CI scope from config. Used by handlers as fallback when
        the caller does not supply a workflow argument."""
        return CIRunScope(workflow=self.config.ci.workflow, event=self.config.ci.event)

    def set_active_recipe_snapshot(
        self,
        snapshot: ActiveRecipeRuntimeSnapshot | None,
        *,
        legacy_steps: Mapping[str, Any] | None = None,
        kitchen_open: bool = False,
    ) -> None:
        """Atomically install or clear the active-recipe runtime snapshot.

        This is the single canonical transition API for the active recipe.
        Passing ``None`` clears the state; ``kitchen_open=True`` preserves the
        historical empty-collection sentinel for an open kitchen with no
        loaded recipe. Passing a snapshot installs it and synchronously
        refreshes the derived legacy fields so existing tool
        handlers that read ``active_recipe_packs``/``active_recipe_features``/
        ``active_recipe_steps``/``active_recipe_ingredients`` continue to work
        without a second lookup. ``legacy_steps`` carries parsed ``RecipeStep``
        objects only for handlers not yet migrated to sealed specs; runtime
        admission consumes ``self.active_recipe_snapshot`` directly.
        """
        self.active_recipe_snapshot = snapshot
        if snapshot is None:
            empty: frozenset[str] | None = frozenset() if kitchen_open else None
            self.active_recipe_packs = empty
            self.active_recipe_features = empty
            self.active_recipe_steps = {} if kitchen_open else None
            self.active_recipe_ingredients = empty
            self.recipe_name = ""
            self.recipe_content_hash = ""
            self.recipe_composite_hash = ""
            self.recipe_version = ""
            return
        self.active_recipe_packs = frozenset(snapshot.required_packs)
        self.active_recipe_features = frozenset(snapshot.required_features)
        self.active_recipe_steps = (
            dict(legacy_steps)
            if legacy_steps is not None
            else {spec.step_key: spec for spec in snapshot.post_prune_steps}
        )
        self.active_recipe_ingredients = frozenset(
            ing.name for ing in snapshot.normalized_ingredients
        )
        self.recipe_name = snapshot.recipe_kind or ""
        self.recipe_content_hash = snapshot.content_hash
        self.recipe_composite_hash = snapshot.composite_hash
        self.recipe_version = snapshot.recipe_version
