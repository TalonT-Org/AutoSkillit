# types/

Type re-export hub and all typed building blocks for the autoskillit package (IL-0).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-export hub — aggregates `__all__` from all `_type_*.py` modules |
| `_type_enums.py` | All `StrEnum` discriminators (`RetryReason`, `KillReason`, `Severity`, `ObserverStatus`, etc.) |
| `_type_exceptions.py` | Exception hierarchy for recipe loading: `RecipeLoadError`, `ProcessStaleError`, `RecipeNotFoundError` |
| `_type_figure_spec.py` | `FigureSpec` TypedDict and consumer/producer field sets for `yaml:figure-spec` contracts |
| `_type_constants.py` | Retired name registries, skill contracts, orchestration prompt sections, CI/domain constants |
| `_type_constants_env.py` | Environment variable names, session type aliases, context markers, logging keys |
| `_type_constants_registries.py` | Tool registries, pack registries, tool-to-tag mappings, visibility tags |
| `_type_constants_features.py` | Feature gates (FeatureDef, FEATURE_REGISTRY), label lifecycle state machine |
| `_type_session_env.py` | Typed env spec dataclasses for session launch boundaries (`FleetSessionEnv`) |
| `_type_skill_contract.py` | Backend-neutral skill source identities and immutable persisted session contracts |
| `_type_subprocess.py` | `SubprocessResult` dataclass and `SubprocessRunner` protocol |
| `_type_token.py` | `CanonicalTokenUsage` frozen dataclass with factory methods and merge |
| `_type_results_execution.py` | Execution-scoped result dataclasses: `SessionTelemetry`, `RecipeIdentity`, `CIRunScope` |
| `_type_results.py` | Core result dataclasses: `SkillResult`, `ProviderOutcome`, `LoadResult`, `FailureRecord`, `WriteBehaviorSpec`, `ClosureAuthoritySpec`, `closure_authority_spec_from_args` |
| `_type_closure_report.py` | Closure report dataclasses: `ClosureRow`, `ClosureReport`, `CLOSURE_REPORT_SCHEMA_VERSION` |
| `_type_audit_cycle.py` | Frozen audit-cycle authority, artifact reference, disposition, head, and admission decision models |
| `_type_recipe_binding.py` | Frozen tool definitions, binding values/failures, compiled step invocations, and immutable binding projections |
| `_type_recipe_execution.py` | Frozen compiled-execution snapshots, domain-separated invocation/runtime digests, and audit/preflight service protocols |
| `_type_protocols_logging.py` | Protocols: `AuditLog`, `TokenLog`, `TimingLog`, `McpResponseLog`, `GitHubApiLog`, `SupportsDebug`, `SupportsLogger` |
| `_type_protocols_execution.py` | Protocols: `TestRunner`, `HeadlessExecutor`, `OutputPatternResolver`, `WriteExpectedResolver` |
| `_type_protocols_github.py` | Protocols: `GitHubFetcher`, `CIWatcher`, `MergeQueueWatcher` |
| `_type_protocols_workspace.py` | Protocols: `WorkspaceManager`, `CloneManager`, `SessionSkillManager`, `SkillLister`, `SkillResolver` |
| `_type_protocols_recipe.py` | Protocols: `RecipeRepository`, `MigrationService`, `DatabaseReader`, `ReadOnlyResolver` |
| `_type_protocols_infra.py` | Protocols: `GateState`, `BackgroundSupervisor`, `FleetLock`, `QuotaRefreshTask`, `TokenFactory`, `CampaignProtector` |
| `_type_protocols_backend.py` | Protocols: `StreamParser`, `ResultParser`, `EnvPolicy`, `ReadinessProbe`, `SessionLocator`, `CodingAgentBackend` |
| `_type_checkpoint.py` | `SessionCheckpoint` frozen dataclass and `compute_remaining()` helper for session resume |
| `_type_backend.py` | `BackendCapabilities` frozen dataclass, `CLAUDE_CODE_CAPABILITIES` constant, `CmdSpec`, `SkillSessionConfig`, `ClaudeEventData`, `CodexEventData`, `SessionEvent`, `AgentSessionResult` |
| `_type_recipe_delivery.py` | Typed Codex recipe budgets, protected-host evidence definitions, requests, attestations, and delivery decisions |
| `_type_recipe_sections.py` | Recipe-section schema validation plus canonical section, element, and plan digest helpers |
| `_type_capture.py` | `CaptureEntrySpec` and `CaptureValueTypeError` for typed capture contract enforcement |
| `_type_dispatch_identity.py` | `DispatchIdentity` frozen value object, `PromptContractError`, and `assert_prompt_sentinel` for sentinel contract enforcement |
| `_type_helpers.py` | Text processing, skill-name extraction, and shared content-free validation utilities |
| `_type_inspector.py` | Health Inspector types: `InspectorEvidence`, `InspectorVerdict`, `InspectorCallback` (issue #3533) |
| `_type_invariant_registry.py` | Invariant registry: `InvariantDef` dataclass and `INVARIANT_REGISTRY` mapping prose prohibitions to runtime gates |
| `_type_intake_policy.py` | Codex context-intake rule registry: `IntakeRuleDef`, `CODEX_INTAKE_RULES`, rendered digest, byte budgets |
| `_type_phoropter.py` | Phoropter family/phase types: `PhoropterPrescription`, `ReadingToken`, `READING_TOKEN_PATTERN`, `PhoropterPhaseSkip`, `CrossDomainPrescription`, `CrossDomainAssessment` |
| `_type_resume.py` | `ResumeSpec` discriminated union: `NoResume | BareResume | NamedResume` |
| `_type_plugin_source.py` | `DirectInstall` (projection input) and `ProjectedPluginRoot` (the sole `PluginSource`) |
| `_type_tradition_manifest.py` | `TraditionManifest`, `LensEntry`, `DialingConfig` frozen dataclasses with `from_dict`/`from_yaml_path` loaders |
| `_type_context_admission.py` | Frozen content-free identities, events, records, effects, states, and coverage definitions for context admission |

## Architecture Notes

Internal dependency DAG: enums -> constants_registries -> constants_features; enums -> results -> protocols -> helpers; enums -> phoropter; enums + phoropter -> tradition_manifest. `_type_intake_policy` is a DAG leaf — stdlib-only, zero sibling imports. All modules have zero `autoskillit` imports outside this sub-package (IL-0 hard constraint). Production code imports from `autoskillit.core`, not from this package directly.

## Extension Bundle Pattern

New feature fields go on frozen dataclass bundles (`InfraOutcome`, `ProviderOutcome`), not flat on `SkillResult`. Bundles are embedded as `field(default_factory=...)` on `SkillResult`. The `to_json()` method flattens bundle fields to top-level JSON keys for backward compatibility.
