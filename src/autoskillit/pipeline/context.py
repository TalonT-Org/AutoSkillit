"""ToolContext: explicit dependency container for server tool implementations.

pipeline/ module — the only pipeline sub-module that imports from config/.
Replaces two mutable module-level singletons in server.py:
  _config, _tools_enabled
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    AuditLog,
    BackgroundSupervisor,
    CampaignProtector,
    CIRunScope,
    CIWatcher,
    CloneManager,
    CodingAgentBackend,
    CompletionRequiredResolver,
    ContextAdmissionLedger,
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
    PluginArtifactAuthority,
    QuotaRefreshTask,
    ReadOnlyResolver,
    RecipeExecutionFactory,
    RecipeExecutionLock,
    RecipeRepository,
    ServeOverridesSnapshot,
    SessionSkillManager,
    SkillContractResolver,
    SkillResolver,
    SkillSessionContractStore,
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
from autoskillit.pipeline.recipe_initialization import (
    NoActiveRecipe,
    RecipeInitializationState,
)

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
    plugin_authority:     PluginArtifactAuthority — lazy authority that acquires one
                          exact artifact incarnation for each physical child launch.
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
    skill_contract_resolver: SkillContractResolver — resolves a skill's full contract
                          from skill contracts.
    quota_refresh_task:   QuotaRefreshTask — cancellable handle for the kitchen-scoped
                          quota refresh background task.
    fleet_lock:           FleetLock — semaphore-style guard for concurrent fleet dispatch.
    build_protected_campaign_ids: CampaignProtector — resolves campaign IDs exempt from
                          log retention purge.
    session_skill_manager: SessionSkillManager — manages per-session ephemeral skill dirs
    skill_resolver:       SkillResolver — resolves skill names to source tier
    skill_session_contract_store: SkillSessionContractStore — binds projected skill
                          contracts and snapshots to resumable backend session IDs.
    context_admission_ledger: ContextAdmissionLedger — durable, shadow-only cumulative
                          context accounting and recovery service.
    kitchen_id:           UUID string assigned when open_kitchen fires; scopes token telemetry
                          to the current kitchen session lifetime.
    active_recipe_packs:  frozenset[str] | None — pack names declared by the loaded recipe
                          (frozenset() when kitchen open but no recipe loaded; None when closed)
    active_recipe_features: frozenset[str] | None — feature names declared by the loaded recipe
                          (frozenset() when kitchen open but no recipe loaded; None when closed)
    active_recipe_steps:  dict[str, Any] | None — step definitions cached from the loaded recipe
                          ({} when kitchen open but no recipe loaded; None when closed)
    active_recipe_ingredients: frozenset[str] | None — ingredient keys declared by the loaded
                          recipe (frozenset() when kitchen open but no recipe loaded; None when
                          closed)
    recipe_initialization_state: sole INITIALIZING/READY authority for the active
                          generation, its staged snapshot, progress, and installed execution.
    recipe_execution_lock: RecipeExecutionLock — serializes installation, lookup, and
                          cleanup of the active compiled execution state.
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
    plugin_authority: PluginArtifactAuthority
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
    skill_contract_resolver: SkillContractResolver | None = field(default=None)
    recipe_execution_factory: RecipeExecutionFactory | None = field(default=None)
    backend: CodingAgentBackend | None = field(default=None)
    session_skill_manager: SessionSkillManager | None = field(default=None)
    skill_resolver: SkillResolver | None = field(default=None)
    skill_session_contract_store: SkillSessionContractStore = field(default=_MISSING)
    context_admission_ledger: ContextAdmissionLedger = field(default=_MISSING)
    recipe_name: str = field(default="")
    recipe_content_hash: str = field(default="")
    recipe_composite_hash: str = field(default="")
    recipe_version: str = field(default="")
    gate_infrastructure_ready: bool = field(default=False)
    kitchen_id: str = field(default="")
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
    recipe_initialization_state: RecipeInitializationState = field(default_factory=NoActiveRecipe)
    recipe_execution_lock: RecipeExecutionLock = field(
        default_factory=threading.RLock,
        repr=False,
    )

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
        if self.skill_session_contract_store is _MISSING:
            raise TypeError(
                "skill_session_contract_store must be supplied explicitly. "
                "Use make_context() or pass an isolated store directly."
            )
        if self.context_admission_ledger is _MISSING:
            raise TypeError(
                "context_admission_ledger must be supplied explicitly. "
                "Use make_context() or pass an isolated ledger directly."
            )
        if self.background is None:
            self.background = DefaultBackgroundSupervisor(audit=self.audit)

    @property
    def default_ci_scope(self) -> CIRunScope:
        """Build the default CI scope from config. Used by handlers as fallback when
        the caller does not supply a workflow argument."""
        return CIRunScope(workflow=self.config.ci.workflow, event=self.config.ci.event)
