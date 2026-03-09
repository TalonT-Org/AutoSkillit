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
    CIWatcher,
    CloneManager,
    DatabaseReader,
    GatePolicy,
    GitHubFetcher,
    HeadlessExecutor,
    MigrationService,
    OutputPatternResolver,
    RecipeRepository,
    SessionSkillManager,
    SubprocessRunner,
    TestRunner,
    TimingStore,
    TokenStore,
    WorkspaceManager,
)


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
    session_skill_manager: SessionSkillManager — manages per-session ephemeral skill dirs
    """

    config: AutomationConfig
    audit: AuditStore
    token_log: TokenStore
    timing_log: TimingStore
    gate: GatePolicy
    plugin_dir: str
    runner: SubprocessRunner | None
    executor: HeadlessExecutor | None = field(default=None)
    tester: TestRunner | None = field(default=None)
    recipes: RecipeRepository | None = field(default=None)
    migrations: MigrationService | None = field(default=None)
    db_reader: DatabaseReader | None = field(default=None)
    workspace_mgr: WorkspaceManager | None = field(default=None)
    clone_mgr: CloneManager | None = field(default=None)
    github_client: GitHubFetcher | None = field(default=None)
    ci_watcher: CIWatcher | None = field(default=None)
    output_pattern_resolver: OutputPatternResolver | None = field(default=None)
    session_skill_manager: SessionSkillManager | None = field(default=None)
