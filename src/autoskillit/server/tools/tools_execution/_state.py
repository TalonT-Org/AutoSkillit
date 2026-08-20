"""State dataclass shared across all ``run_skill`` dispatch phases.

Bundles every dispatch-scope local read across phase-function boundaries into
a single mutable container.

Construction contract
----------------------

Per D6, ``_RunSkillDispatchState`` is constructed as the first statement
inside ``run_skill``'s outer ``try:``, after ``_get_ctx()`` has already
succeeded — so ``tool_ctx`` is guaranteed present, never ``None``, at
construction time. The constructor accepts exactly ``run_skill``'s 21 tool
parameters plus ``ctx`` and ``tool_ctx``; every other field carries a default
and is populated by whichever phase function computes it.

Access convention
------------------

Fields without a leading underscore are mostly ``run_skill``'s tool-function
parameters (e.g. ``skill_command``, ``cwd``, ``skill_inputs``) together with
roughly twenty dispatch-computed values consumed by later phases (e.g.
``invocation``, ``projection_context``, ``target_name``,
``child_skill_command``, ``resolved_command``, ``write_spec``,
``closure_spec``, ``is_read_only``, ``completion_required``,
``invocation_marker``, ``effective_order_id``, ``effective_model``). Fields
with a leading underscore are internal locals captured during one phase and
read by a later one. The convention is suggestive, not enforced; consult the
section banner over each group when the boundary is unclear.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autoskillit.server.tools._execution_helpers import _RunSkillContractLifecycle

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextvars import Token
    from pathlib import Path

    from autoskillit.server.tools.tools_execution._run_skill_prepare import _ExplorerLaunchLease
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
    tool_ctx: ToolContext
    ctx: Context
    contract_lifecycle: _RunSkillContractLifecycle = field(
        default_factory=_RunSkillContractLifecycle
    )

    # --- Dispatch bootstrap (timing, tracker authority, explorer launch) ---
    _start: float = 0.0
    _sn_token: Token[str] | None = None
    _oid_token: Token[str] | None = None
    _tracker_target: TrackerAuthorityTarget | None = None
    _tracker_authority: TrackerAuthorityReadResult | None = None
    _tracker_key: TrackerParticipantKey | None = None
    _tracker_lease: ArtifactLease | None = None
    _cleanup_session_id: str | None = None
    _explorer_parent_identity: tuple[Path, str] | None = None
    _explorer_launch_lease: _ExplorerLaunchLease | None = None

    # --- Resolved contracts (execution + audit/preflight) ---
    _installed_execution: InstalledRecipeExecution | None = None
    _contract_store: SkillSessionContractStore | None = None
    _stored_contract_entry: StoredSkillSessionContract | None = None
    _session_contract: SkillSessionContract | None = None
    _session_snapshot: dict[str, str] | None = None
    _native_shell_capture_decision: NativeShellCaptureDecision | None = None
    _managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None
    _resume_backend_obj: CodingAgentBackend | None = None
    _resume_backend_authority: BackendAuthority | None = None
    _resume_launch_contract: ResolvedLaunchContract | None = None
    _effective_skill_resolver: SkillResolver | None = None
    invocation: EffectiveSkillInvocationAuthority | None = None
    projection_context: SkillProjectionContext | None = None
    target_name: str | None = None
    _preflight_result: VerifiedInputPreflightResult | None = None
    _bound_recipe_inputs: tuple[tuple[str, BoundScalar], ...] = ()
    _invocation_template: InvocationTemplate | None = None
    _audit_reservation: AuditIdentityReservation | None = None
    _audit_preflight_steps: tuple[str, ...] = ()
    _target_contract: RecipeSkillContract | None = None
    _audit_publication: AuditAuthorityPublicationSpec | None = None

    # --- Dispatch / recipe execution ---
    child_skill_command: str = ""
    _claims_recipe_execution: bool = False
    _dynamic_recipe_call: bool = False
    _audit_output_mode: AuditOutputMode | None = None
    _clone_allowed_root: Path | None = None
    _slot_intent_digest: str | None = None
    _bound_input_map: dict[str, BoundScalar] | None = None
    _prior_input_field: str | None = None
    _prior_path: str | None = None
    _recipe_execution_key: RecipeExecutionId | None = None
    _audited_plan_refs: tuple[ArtifactRef, ...] | None = None
    _cycle_id: str | None = None
    _scope_id: str | None = None
    _part_id: str | None = None
    _parent_digest: str | None = None
    _reservation_outcome: AuditReservationOutcome | None = None
    _replay: AuditOutcome | None = None
    _resumed: AuditMaterializationResult | None = None
    _authority: AuditCycleAuthority | None = None
    _published: AuditMaterializationResult | None = None

    # --- Provider / backend ---
    effective_order_id: str = ""
    _cfg: AutomationConfig | None = None
    _in_fleet_dispatch: bool = False
    _inspector_model: str | None = None
    effective_model: str = ""
    provider_extras: dict[str, str] | None = None
    profile_name_out: str | None = None
    _profile: str = ""
    _env_dict: dict[str, str] = field(default_factory=dict)
    _mo_recipe_map: dict[str, str] | None = None
    _step_mo: str | None = None
    _stored_contract: SkillSessionContract | None = None
    resolved_command: str | None = None
    _effective_skill_contract: EffectiveSkillInvocationAuthority | SkillSessionContract | None = (
        None
    )
    _explicit_resolution: BackendPinResolution | None = None
    _skill_caps: frozenset[str] = frozenset()
    _sandbox_overrides: frozenset[str] = frozenset()
    _network_access: bool | None = None
    _backend_authority: BackendAuthority | None = None
    _effective_backend_obj: CodingAgentBackend | None = None
    _explicit_binary: str | None = None
    _fresh_parent_sandbox_mode: str | None = None
    _active_exploration_applicabilities: frozenset[ExplorationVectorApplicabilityId] | None = None

    # --- Metadata (dispatch metadata + lineage/scope) ---
    expected_output_patterns: list[str] | None = None
    write_spec: WriteBehaviorSpec | None = None
    _skill_contract: RecipeSkillContract | None = None
    closure_spec: ClosureAuthoritySpec | None = None
    closure_report_root: Path | None = None
    _closure_root: Path | None = None
    write_watch_dirs: list[Path] | None = None
    _default_temp: Path | None = None
    is_read_only: bool = False
    scope_discipline_skill: bool = False
    completion_required: bool = False
    invocation_marker: str = ""
    skill_add_dirs: list[ValidatedAddDir] | None = None
    replay_snapshot_used: bool = False
    _runner: SubprocessRunner | None = None
    _ephemeral_root: Path | None = None
    _restored: ValidatedAddDir | None = None
    _capability_contract: SkillProjectionBinding | None = None
    _execution_identity: ExecutionIdentity | None = None
    _lineage_store: ManagedHeadlessSessionLineageStore | None = None
    _lineage_preparation: SkillNativeShellLineagePreparation | None = None

    # --- Write prefix / marker ---
    allowed_write_prefix: str | None = None
    allowed_write_prefixes: tuple[str, ...] | None = None
    _skill_temp_name: str | None = None
    _marker_dir: Path | None = None
    _launch_id: str = ""
    _session_registry: Mapping[str, Mapping[str, str]] | None = None
    _registry_row: Mapping[str, str] | None = None
    _registered_session_id: str | None = None
    _caller_hook_session_id: str | None = None

    # --- Executor result ---
    skill_result: SkillResult | None = None

    # --- Finalize-writable (single helper site mutates these) ---
    _audit_outcome_to_finalize: AuditOutcome | None = None
    _semantic_path: Path | None = None
    _materialized: AuditMaterializationResult | None = None
    _materialized_status: AuditOutcomeStatus | None = None
    _timeout_exc: Exception | None = None
    _timeout_result: SkillResult | None = None
    _parsed: dict[str, Any] | None = None
    _missing: set[str] | None = None
    _shaped_response: str | None = None
    _replay_payload: dict[str, Any] | None = None
    _crashed_result: SkillResult | None = None
    _unhandled_result: SkillResult | None = None
    _cancelled_result: SkillResult | None = None
    _completion_authority: RunSkillCompletionAuthority | None = None
    _sid: str | None = None
    _ssm: SessionSkillManager | None = None
    _cleanup_dir: Path | None = None
    _codex_fallback: Path | None = None
    _completion_invocation_id: str | None = None
