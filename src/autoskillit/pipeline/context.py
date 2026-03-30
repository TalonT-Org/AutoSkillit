"""ToolContext: explicit dependency container for server tool implementations.

pipeline/ module — the only pipeline sub-module that imports from config/.
Replaces two mutable module-level singletons in server.py:
  _config, _tools_enabled
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    AuditStore,
    BackgroundSupervisor,
    CIRunScope,
    CIWatcher,
    CloneManager,
    DatabaseReader,
    GatePolicy,
    GitHubFetcher,
    HeadlessExecutor,
    McpResponseStore,
    MergeQueueWatcher,
    MigrationService,
    OutputPatternResolver,
    RecipeRepository,
    SessionSkillManager,
    SubprocessRunner,
    TargetSkillResolver,
    TestRunner,
    TimingStore,
    TokenStore,
    WorkspaceManager,
    WriteExpectedResolver,
)
from autoskillit.pipeline.background import DefaultBackgroundSupervisor
from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog


@dataclass
class ToolContext:
    """Single dependency container threaded through all MCP tool implementations.

    Constructed once in cli.py serve() via server._factory.make_context() and
    injected into server.py via server._initialize(ctx). Tests construct isolated
    instances per-test to avoid global state leakage.

    Fields
    ------
    config:               AutomationConfig loaded from .autoskillit/config.yaml
    audit:                AuditStore — records pipeline failures
    token_log:            TokenStore — per-step token tracking
    timing_log:           TimingStore — per-step wall-clock duration tracking
    response_log:         McpResponseStore — per-tool MCP response size tracking
    gate:                 GatePolicy — enables/disables gated tools
    plugin_dir:           Absolute path string to the autoskillit package directory
    runner:               SubprocessRunner implementation (DefaultSubprocessRunner in production,
                          MockSubprocessRunner in tests)
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
    session_skill_manager: SessionSkillManager — manages per-session ephemeral skill dirs
    skill_resolver:       TargetSkillResolver — resolves skill names to source tier
    kitchen_id:           UUID string assigned when open_kitchen fires; scopes token telemetry
                          to the current kitchen session lifetime.
    active_recipe_packs:  frozenset[str] | None — pack names declared by the loaded recipe
                          (frozenset() when kitchen open but no recipe loaded; None when closed)
    """

    config: AutomationConfig
    audit: AuditStore
    token_log: TokenStore
    timing_log: TimingStore
    gate: GatePolicy
    plugin_dir: str
    runner: SubprocessRunner | None
    response_log: McpResponseStore = field(default_factory=DefaultMcpResponseLog)
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
    background: BackgroundSupervisor | None = field(default=None)
    output_pattern_resolver: OutputPatternResolver | None = field(default=None)
    write_expected_resolver: WriteExpectedResolver | None = field(default=None)
    session_skill_manager: SessionSkillManager | None = field(default=None)
    skill_resolver: TargetSkillResolver | None = field(default=None)
    kitchen_id: str = field(default="")
    active_recipe_packs: frozenset[str] | None = field(default_factory=lambda: None)

    def __post_init__(self) -> None:
        if self.background is None:
            self.background = DefaultBackgroundSupervisor(audit=self.audit)

    @property
    def default_ci_scope(self) -> CIRunScope:
        """Build the default CI scope from config. Used by handlers as fallback when
        the caller does not supply a workflow argument."""
        return CIRunScope(workflow=self.config.ci.workflow)
