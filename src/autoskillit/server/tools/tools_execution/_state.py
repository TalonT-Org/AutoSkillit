"""State dataclass shared between ``_run_skill_dispatch`` and ``_run_skill_finalize``.

Bundles every dispatch-scope local read by the finalize block into a single
mutable container.

Access convention
-----------------

Fields without an underscore prefix are mostly tool-function parameters
of ``run_skill`` (e.g. ``skill_command``, ``cwd``, ``skill_inputs``) and a
handful of dispatch-computed values consumed by the finalize block (e.g.
``invocation``, ``projection_context``, ``target_name``, ``child_skill_command``,
``resolved_command``, ``write_spec``, ``closure_spec``, ``is_read_only``).
Fields with a leading underscore are internal locals captured during
dispatch and finalized. The convention is suggestive, not enforced; consult
the section banner over each group when the boundary is unclear.

Decomposition note (review Finding 3)
--------------------------------------

The dataclass has ~150 fields spanning 8 commented section groups. The PR
review recommended splitting into purpose-bounded sub-dataclasses. Deferred to
the Step 2 dispatch/finalize split, where the empirical call graph will
establish which fields are read together. Sub-dataclasses inside this file are
permitted at any time without affecting the
``test_tools_execution_decomposition_has_expected_siblings`` test, which checks
sibling filenames only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextvars import Token
    from pathlib import Path

    from fastmcp import Context

    from autoskillit.config import AutomationConfig
    from autoskillit.core import (
        ArtifactLease,
        AuditCycleAuthority,
        AuditIdentityReservation,
        AuditMaterializationResult,
        AuditOutcome,
        AuditOutcomeStatus,
        AuditReservationOutcome,
        BackendAuthority,
        BackendPinResolution,
        BoundScalar,
        ClosureAuthoritySpec,
        CodingAgentBackend,
        EffectiveSkillInvocationAuthority,
        ExecutionIdentity,
        InstalledRecipeExecution,
        InvocationTemplate,
        ManagedHeadlessSessionLineageRef,
        ManagedHeadlessSessionLineageStore,
        NativeShellCaptureDecision,
        RecipeExecutionId,
        ResolvedLaunchContract,
        RunSkillCompletionAuthority,
        SessionSkillManager,
        SkillProjectionBinding,
        SkillResolver,
        SkillResult,
        SkillSessionContractStore,
        StoredSkillSessionContract,
        SubprocessRunner,
        TrackerAuthorityReadResult,
        TrackerAuthorityTarget,
        TrackerParticipantKey,
        ValidatedAddDir,
        VerifiedInputPreflightResult,
        WriteBehaviorSpec,
    )
    from autoskillit.core.types._type_audit_cycle import ArtifactRef
    from autoskillit.core.types._type_skill_contract import (
        ExplorationVectorApplicabilityId,
        SkillSessionContract,
    )
    from autoskillit.pipeline import ToolContext
    from autoskillit.recipe import (
        AuditAuthorityPublicationSpec,
        AuditOutputMode,
    )
    from autoskillit.recipe import (
        SkillContract as RecipeSkillContract,
    )
    from autoskillit.server._misc import SkillProjectionContext
    from autoskillit.server.tools._execution_helpers import _RunSkillContractLifecycle
    from autoskillit.server.tools._native_shell_capture._lineage import (
        SkillNativeShellLineagePreparation,
    )


@dataclass(slots=True, kw_only=True)
class _RunSkillDispatchState:
    # --- Tool inputs (function parameters and runtime context) ---
    skill_command: str
    cwd: str
    order_id: str
    step_name: str
    model: str
    recipe_execution_id: str
    invocation_template_digest: str
    step_provider: str
    stale_threshold: int | None
    idle_output_timeout: int | None
    output_dir: str
    resume_session_id: str
    retry_after_audit_attempt_id: str
    native_shell_capture_mode: str
    closure_authority_path: str
    closure_authority_hash: str
    closure_plan_paths: str
    closure_base_sha: str
    closure_diff_sha: str
    closure_target_sha: str
    skill_inputs: dict[str, str | int | bool] | None
    tool_ctx: ToolContext | None
    ctx: Context
    contract_lifecycle: _RunSkillContractLifecycle

    # --- Dispatch bootstrap (timing, tracker authority, explorer launch) ---
    _start: float
    _sn_token: Token | None
    _oid_token: Token | None
    _tracker_target: TrackerAuthorityTarget | None
    _tracker_authority: TrackerAuthorityReadResult | None
    _tracker_key: TrackerParticipantKey | None
    _tracker_lease: ArtifactLease | None
    _cleanup_session_id: str | None
    _explorer_parent_identity: tuple[Path, str] | None
    _explorer_launch_lease: Any  # _ExplorerLaunchLease | None (defined in tools_execution.py)

    # --- Resolved contracts (execution + audit/preflight) ---
    _installed_execution: InstalledRecipeExecution | None
    _contract_store: SkillSessionContractStore
    _stored_contract_entry: StoredSkillSessionContract | None
    _session_contract: SkillSessionContract | None
    _session_snapshot: dict[str, str] | None
    _native_shell_capture_decision: NativeShellCaptureDecision | None
    _managed_lineage_ref: ManagedHeadlessSessionLineageRef | None
    _resume_backend_obj: CodingAgentBackend | None
    _resume_backend_authority: BackendAuthority | None
    _resume_launch_contract: ResolvedLaunchContract | None
    _effective_skill_resolver: SkillResolver | None
    invocation: EffectiveSkillInvocationAuthority | None
    projection_context: SkillProjectionContext | None
    target_name: str | None
    _preflight_result: VerifiedInputPreflightResult | None
    _bound_recipe_inputs: tuple[tuple[str, BoundScalar], ...]
    _invocation_template: InvocationTemplate | None
    _audit_reservation: AuditIdentityReservation | None
    _audit_preflight_steps: tuple[str, ...]
    _target_contract: RecipeSkillContract | None
    _audit_publication: AuditAuthorityPublicationSpec | None

    # --- Dispatch / recipe execution ---
    child_skill_command: str
    _claims_recipe_execution: bool
    _dynamic_recipe_call: bool
    _audit_output_mode: AuditOutputMode | None
    _clone_allowed_root: Path
    _slot_intent_digest: str | None
    _bound_input_map: dict[str, BoundScalar] | None
    _prior_input_field: str | None
    _prior_path: str | None
    _recipe_execution_key: RecipeExecutionId | None
    _audited_plan_refs: tuple[ArtifactRef, ...] | None
    _cycle_id: str | None
    _scope_id: str | None
    _part_id: str | None
    _parent_digest: str | None
    _reservation_outcome: AuditReservationOutcome | None
    _replay: AuditOutcome | None
    _resumed: AuditMaterializationResult | None
    _authority: AuditCycleAuthority | None
    _published: AuditMaterializationResult | None

    # --- Provider / backend ---
    _effective_order_id: str
    _cfg: AutomationConfig
    _in_fleet_dispatch: bool
    _inspector_model: str | None
    _effective_model: str
    provider_extras: dict[str, str] | None
    profile_name_out: str | None
    _profile: str
    _env_dict: dict[str, str]
    _mo_recipe_map: dict[str, str] | None
    _step_mo: str | None
    _stored_contract: SkillSessionContract | None
    resolved_command: str
    _effective_skill_contract: EffectiveSkillInvocationAuthority | SkillSessionContract | None
    _explicit_resolution: BackendPinResolution | None
    _skill_caps: frozenset[str]
    _sandbox_overrides: frozenset[str]
    _network_access: bool | None
    _backend_authority: BackendAuthority | None
    _effective_backend_obj: CodingAgentBackend | None
    _explicit_binary: str | None
    _fresh_parent_sandbox_mode: str | None
    _active_exploration_applicabilities: frozenset[ExplorationVectorApplicabilityId] | None

    # --- Metadata (dispatch metadata + lineage/scope) ---
    expected_output_patterns: list[str] | None
    write_spec: WriteBehaviorSpec | None
    _skill_contract: RecipeSkillContract | None
    closure_spec: ClosureAuthoritySpec | None
    closure_report_root: Path | None
    _closure_root: Path | None
    write_watch_dirs: list[Path] | None
    _default_temp: Path | None
    is_read_only: bool
    scope_discipline_skill: bool
    completion_required: bool
    invocation_marker: str
    skill_add_dirs: list[ValidatedAddDir] | None
    replay_snapshot_used: bool
    _runner: SubprocessRunner | None
    _ephemeral_root: Path | None
    _restored: ValidatedAddDir | None
    _capability_contract: SkillProjectionBinding
    _execution_identity: ExecutionIdentity | None
    _lineage_store: ManagedHeadlessSessionLineageStore
    _lineage_preparation: SkillNativeShellLineagePreparation

    # --- Write prefix / marker ---
    allowed_write_prefix: str | None
    allowed_write_prefixes: tuple[str, ...] | None
    _skill_temp_name: str | None
    _marker_dir: Path | None
    _launch_id: str
    _session_registry: Mapping[str, Mapping[str, str]] | None
    _registry_row: Mapping[str, str] | None
    _registered_session_id: str | None
    _caller_hook_session_id: str | None

    # --- Finalize-writable (single helper site mutates these) ---
    _audit_outcome_to_finalize: AuditOutcome | None
    _semantic_path: Path | None
    _materialized: AuditMaterializationResult | None
    _materialized_status: AuditOutcomeStatus | None
    _timeout_exc: Exception | None
    _timeout_result: SkillResult | None
    _parsed: dict[str, Any] | None
    _missing: set[str] | None
    _shaped_response: str | None
    _replay_payload: dict[str, Any] | None
    _crashed_result: SkillResult | None
    _unhandled_result: SkillResult | None
    _cancelled_result: SkillResult | None
    _completion_authority: RunSkillCompletionAuthority | None
    _sid: str | None
    _ssm: SessionSkillManager | None
    _cleanup_dir: Path | None
    _codex_fallback: Path | None
    # Only field with a default. The empty-string seed matches the original
    # `tools_execution.py` semantics (`_completion_invocation_id = ""` before
    # finalize runs).
    _completion_invocation_id: str = ""
