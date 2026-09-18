# types/

Type re-export hub and all typed building blocks for the autoskillit package (IL-0).

## Architecture Notes

Internal dependency DAG: enums -> recipe_sections -> constants_registries -> constants_features; enums -> results -> protocols -> helpers; enums -> phoropter; enums + phoropter -> tradition_manifest; enums -> exceptions; exploration -> exceptions. `_type_intake_policy` is a DAG leaf — stdlib-only, zero sibling imports. `_type_recipe_sections.py` owns recipe-section registry and pagination-policy construction; `_type_constants_registries.py` imports its ten public names as a facade. All modules have zero `autoskillit` imports outside this sub-package (IL-0 hard constraint). Production code imports from `autoskillit.core`, not from this package directly.

## Extension Bundle Pattern

New feature fields go on frozen dataclass bundles (`InfraOutcome`, `ProviderOutcome`), not flat on `SkillResult`. Bundles are embedded as `field(default_factory=...)` on `SkillResult`. The `to_json()` method flattens bundle fields to top-level JSON keys for backward compatibility.

## Concern map

Each direct Python file has one responsibility:

### Public re-exports

- `__init__.py` — Core type contracts: re-export hub.

### Audit contracts

- `_type_audit_admission.py` — Immutable value contracts for server-owned audit admission and publication.
- `_type_audit_admission_artifact_ownership.py` — Audit artifact field-ownership definitions and registry.
- `_type_audit_admission_ledger.py` — Frozen contracts for the parent-owned durable audit admission ledger.
- `_type_audit_admission_reference_identity.py` — Audit reference-identity definitions and calculation.
- `_type_audit_admission_validation.py` — Shared validation helpers for immutable audit-admission contracts.
- `_type_audit_artifact_ref.py` — Immutable audit artifact reference value object.
- `_type_audit_cycle_authority.py` — Immutable audit-cycle authority value objects.
- `_type_audit_cycle_disposition.py` — Immutable audit-cycle plan-disposition value objects.
- `_type_audit_protocols.py` — Narrow IL-0 service protocols for server-owned audit publication.

### Context admission contracts

- `_type_context_admission.py` — Stable facade re-exporting context-admission protocol-v1 contract values.
- `_type_context_admission_base.py` — Shared serialization and validation for context-admission contracts.
- `_type_context_admission_coverage.py` — Static context-admission producer coverage contract.
- `_type_context_admission_effects.py` — Closed context-admission publication-effect contract.
- `_type_context_admission_events.py` — Closed context-admission event contract.
- `_type_context_admission_identities.py` — Context-admission scalar identities and lineage values.
- `_type_context_admission_persistence.py` — Process-local control surface for context-admission persistence.
- `_type_context_admission_persistence_envelope.py` — Durable boundary types for context-admission persistence.
- `_type_context_admission_records.py` — Context-admission snapshots, manifests, and lifecycle records.
- `_type_context_admission_states.py` — Context-admission aggregate state, transition, and replay values.

### Constants and registries

- `_type_constants.py` — Retired name registries, skill contracts, orchestration prompt sections, CI/domain constants.
- `_type_constants_durable_writers.py` — Durable-artifact writer registry — forces every function that writes an artifact with a lifetime exceeding the writing process under a relocatability or machine-local-detection obligation.
- `_type_constants_env.py` — Environment variable names, session type aliases, context markers, logging keys.
- `_type_constants_features.py` — Feature gates (FeatureDef, FEATURE_REGISTRY), label lifecycle state machine.
- `_type_constants_registries.py` — Tool registries, pack registries, tool-to-tag mappings, visibility tags.
- `_type_constants_retirements.py` — Retirement and UNAFFECTED-skill registries.
- `_type_constants_skill_contract.py` — Skill-contract remediation registry.

### Service protocols

- `_type_protocols_backend.py` — Backend abstraction protocol definitions.
- `_type_protocols_execution.py` — Execution-layer protocol definitions.
- `_type_protocols_github.py` — GitHub integration protocol definitions.
- `_type_protocols_infra.py` — Infrastructure and pipeline-control protocol definitions.
- `_type_protocols_logging.py` — Logging and observer protocol definitions.
- `_type_protocols_recipe.py` — Recipe and data access protocol definitions.
- `_type_protocols_workspace.py` — Workspace and skill management protocol definitions.

### Recipe contracts

- `_type_recipe_binding.py` — Frozen recipe-step binding and phoropter value objects.
- `_type_recipe_delivery.py` — Typed recipe-delivery budget, provenance, and decision contracts.
- `_type_recipe_execution.py` — Immutable recipe-execution attestation and admission contracts.
- `_type_recipe_sections.py` — Recipe-section schema validation and canonical digest helpers.

### Result contracts

- `_type_results.py` — Core result dataclasses — universal types.
- `_type_results_execution.py` — Execution-scoped result dataclasses.
- `_type_results_records.py` — Leaf result and persisted-index record contracts.

### Other IL-0 contracts

- `_type_backend.py` — Backend capability declaration type.
- `_type_capture.py` — Capture type contracts for the campaign dispatch capture chain.
- `_type_checkpoint.py` — Session checkpoint for resume progress tracking.
- `_type_closure_report.py` — Closure-mode report schema for audit-impl (IL-0, stdlib-only).
- `_type_dimensions.py` — Dimension-safe token, UTF-8 byte, and serialized-char limits.
- `_type_dispatch_identity.py` — Dispatch identity value object — single source of truth for all sentinel strings.
- `_type_enums.py` — Core StrEnum discriminators.
- `_type_enums_context_admission.py` — Context-admission StrEnum discriminators.
- `_type_exceptions.py` — Exception types for recipe loading failures.
- `_type_execution_identity.py` — Cycle-free execution identity and backend-resolution types.
- `_type_exploration.py` — Immutable, deterministic contracts for read-only repository exploration.
- `_type_figure_spec.py` — Figure specification fields and required producer/consumer schema fields.
- `_type_github_review.py` — Immutable contracts for authoritative GitHub pull-request reviews.
- `_type_github_review_anchor.py` — Immutable, diff-bound inline review anchor authority.
- `_type_helpers.py` — Core skill name resolution and text-processing helpers.
- `_type_inspector.py` — Health Inspector types.
- `_type_install.py` — Typed maintenance-install subprocess boundary shared across package layers.
- `_type_intake_policy.py` — Codex context-intake rule registry — evidence-bound Codex instruction-reading policy.
- `_type_invariant_registry.py` — Invariant registry — prose prohibitions mapped to runtime gates.
- `_type_launch.py` — Portable launch authority and stable launch-contract values.
- `_type_launch_authority.py` — Portable launch authority and provenance values.
- `_type_launch_intent.py` — Closed CLI spelling and interactive-session launch intent values.
- `_type_launch_projection.py` — Non-executable skill projection evidence bound beneath a physical launch.
- `_type_managed_home.py` — Typed authority for AutoSkillit's process home.
- `_type_native_shell_capture.py` — Closed launch-control and managed headless lineage contracts.
- `_type_orchestrator_instruction_surfaces.py` — Orchestrator-facing instruction surface definitions and registry.
- `_type_persisted_formats.py` — Ledger of enums embedded in versioned persisted formats.
- `_type_plugin_source.py` — Import-layer-safe plugin artifact lifecycle value objects.
- `_type_retirement_backstops.py` — Declared safety backstops for destructive plugin-artifact retirement.
- `_type_session_env.py` — Typed env specs for session launch boundaries.
- `_type_skill_contract.py` — Backend-neutral skill source identity contracts.
- `_type_skill_semantics.py` — Backend-neutral semantic requirements declared by portable skills.
- `_type_subprocess.py` — Subprocess execution types and contracts.
- `_type_token.py` — Canonical token usage type.
- `_type_truth.py` — Closed truth-value dialect for values supplied to declared recipe guards.
