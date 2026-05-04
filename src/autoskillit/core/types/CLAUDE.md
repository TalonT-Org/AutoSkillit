# types/

Type re-export hub and all typed building blocks for the autoskillit package (IL-0).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-export hub — aggregates `__all__` from all `_type_*.py` modules |
| `_type_enums.py` | All `StrEnum` discriminators (`RetryReason`, `KillReason`, `Severity`, etc.) |
| `_type_constants.py` | Shared constants: tool lists, env var names, version string |
| `_type_subprocess.py` | `SubprocessResult` dataclass and `SubprocessRunner` protocol |
| `_type_results.py` | Core result dataclasses: `SkillResult`, `LoadResult`, `FailureRecord`, `WriteBehaviorSpec`, `SessionTelemetry` |
| `_type_protocols_logging.py` | Protocols: `AuditLog`, `TokenLog`, `TimingLog`, `McpResponseLog`, `GitHubApiLog`, `SupportsDebug`, `SupportsLogger` |
| `_type_protocols_execution.py` | Protocols: `TestRunner`, `HeadlessExecutor`, `OutputPatternResolver`, `WriteExpectedResolver` |
| `_type_protocols_github.py` | Protocols: `GitHubFetcher`, `CIWatcher`, `MergeQueueWatcher` |
| `_type_protocols_workspace.py` | Protocols: `WorkspaceManager`, `CloneManager`, `SessionSkillManager`, `SkillLister`, `SkillResolver` |
| `_type_protocols_recipe.py` | Protocols: `RecipeRepository`, `MigrationService`, `DatabaseReader`, `ReadOnlyResolver` |
| `_type_protocols_infra.py` | Protocols: `GateState`, `BackgroundSupervisor`, `FleetLock`, `QuotaRefreshTask`, `TokenFactory`, `CampaignProtector` |
| `_type_helpers.py` | Text processing and skill-name extraction utilities |
| `_type_resume.py` | `ResumeSpec` discriminated union: `NoResume | BareResume | NamedResume` |
| `_type_plugin_source.py` | `PluginSource` discriminated union: `DirectInstall | MarketplaceInstall` |

## Architecture Notes

Internal dependency DAG: enums -> constants -> subprocess -> results -> protocols -> helpers. All modules have zero `autoskillit` imports outside this sub-package (IL-0 hard constraint). Production code imports from `autoskillit.core`, not from this package directly.
