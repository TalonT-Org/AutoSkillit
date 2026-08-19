"""State dataclass for the run_skill dispatch/finalize split (REQ-DECOMP-001).

The original ``tools_execution.py`` ran the entire ``run_skill`` flow in
one 1,796-line function. The decomposition splits it into a dispatcher
(``_run_skill_dispatch.py``) and a finalize helper
(``_run_skill_finalize.py``). The finalize helper reads ~150 dispatch-scope
locals; bundling them into one dataclass avoids a 150-parameter signature.

The dataclass is intentionally NOT frozen: ``_completion_invocation_id`` is
written into the state during the finalize block (the helper returns the
result dict that the dispatcher then submits). The dataclass also carries
the ContextVar reset tokens (``_sn_token``, ``_oid_token``) so the
dispatcher-scope ``_run_skill_outer_finally`` can reset the contextvars
after the helper returns — those tokens must live on the state because the
helper's return would otherwise drop them from the dispatcher's frame.

Cross-module types are typed as ``Any`` to keep the state module importable
standalone without dragging in the dispatcher / finalize submodules. The
dispatcher populates the state from typed call sites; the finalize module
reads the typed values via attribute access. Type safety is enforced at
the call sites, not the dataclass field type.
"""

from __future__ import annotations

from contextvars import Token
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import Context

    from autoskillit.pipeline import ToolContext


@dataclass(slots=True)
class _RunSkillDispatchState:
    """Bundles every dispatch-scope local read by the finalize block."""

    # --- Tool-function parameters (lines 994-1015) ---
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

    # --- Runtime context ---
    tool_ctx: ToolContext | None
    ctx: Context
    contract_lifecycle: Any  # _RunSkillContractLifecycle

    # --- Writable: written by finalize helper ---
    _completion_invocation_id: str

    # --- Timing/state ---
    _start: float
    _sn_token: Token | None
    _oid_token: Token | None

    # --- Tracker authority ---
    _tracker_target: Any  # TrackerAuthorityTarget | None
    _tracker_authority: Any  # TrackerAuthorityReadResult | None
    _tracker_key: Any  # TrackerParticipantKey | None
    _tracker_lease: Any  # ArtifactLease | None

    # --- Explorer launch ---
    _cleanup_session_id: str | None
    _explorer_parent_identity: Any  # tuple[Path, str] | None
    _explorer_launch_lease: Any  # _ExplorerLaunchLease | None

    # --- Execution / contract ---
    _installed_execution: Any
    _contract_store: Any
    _stored_contract_entry: Any
    _session_contract: Any  # SkillSessionContract | None
    _session_snapshot: Any
    _native_shell_capture_decision: Any  # NativeShellCaptureDecision | None
    _managed_lineage_ref: Any  # ManagedHeadlessSessionLineageRef | None
    _resume_backend_obj: Any  # CodingAgentBackend | None
    _resume_backend_authority: Any  # BackendAuthority | None
    _resume_launch_contract: Any  # ResolvedLaunchContract | None
    _effective_skill_resolver: Any
    invocation: Any  # EffectiveSkillInvocationAuthority | None
    projection_context: Any  # SkillProjectionContext | None
    target_name: str | None

    # --- Audit / preflight ---
    _preflight_result: Any
    _bound_recipe_inputs: Any
    _invocation_template: Any  # InvocationTemplate | None
    _audit_reservation: Any  # AuditIdentityReservation | None
    _audit_preflight_steps: tuple[str, ...] | None
    _target_contract: Any
    _audit_publication: Any

    # --- Dispatch / recipe execution ---
    child_skill_command: str
    _claims_recipe_execution: bool
    _dynamic_recipe_call: bool
    _audit_output_mode: Any  # AuditOutputMode | None
    _clone_allowed_root: Path
    _slot_intent_digest: str | None
    _bound_input_map: dict | None
    _prior_input_field: str | None
    _prior_path: str | None
    _recipe_execution_key: Any
    _audited_plan_refs: list | None
    _cycle_id: str | None
    _scope_id: str | None
    _part_id: str | None
    _parent_digest: str | None
    _reservation_outcome: Any
    _replay: bool
    _replay_response: str | None
    _resumed: bool
    _resumed_response: str | None
    _authority: Any
    _published: bool
    _published_response: str | None

    # --- Provider / fleet ---
    effective_order_id: str
    _cfg: Any
    _in_fleet_dispatch: bool
    _inspector_model: str | None
    effective_model: str
    provider_extras: dict | None
    profile_name_out: tuple[str, ...] | None
    _profile: Any
    _env_dict: dict | None
    _mo_recipe_map: dict | None
    _step_mo: Any
    _stored_contract: Any  # SkillContract | None

    # --- Backend / projection ---
    resolved_command: str
    _effective_skill_contract: Any
    _explicit_resolution: Any
    _skill_caps: Any
    _sandbox_overrides: frozenset[str] | None
    _network_access: bool | None
    _backend_authority: Any  # BackendAuthority | None
    _effective_backend_obj: Any  # CodingAgentBackend | None
    _explicit_binary: str | None
    _fresh_parent_sandbox_mode: str | None
    _active_exploration_applicabilities: tuple | None

    # --- Dispatch metadata ---
    expected_output_patterns: tuple | None
    write_spec: Any  # WriteBehaviorSpec | None
    _skill_contract: Any  # SkillContract | None
    closure_spec: Any
    closure_report_root: Path | None
    _closure_root: Path | None
    write_watch_dirs: tuple | None
    _default_temp: Path | None
    is_read_only: bool
    scope_discipline_skill: Any
    completion_required: bool
    invocation_marker: Any
    skill_add_dirs: tuple | None  # tuple[ValidatedAddDir, ...] | None
    replay_snapshot_used: bool
    _runner: Any
    _ephemeral_root: Path | None
    _restored: bool

    # --- Lineage / scope ---
    _capability_contract: Any
    _execution_identity: Any  # ExecutionIdentity | None
    _lineage_store: Any
    _lineage_preparation: Any

    # --- Write prefix / marker ---
    allowed_write_prefix: str | None
    allowed_write_prefixes: tuple[str, ...] | None
    _skill_temp_name: str | None
    _marker_dir: Path | None
    _launch_id: str | None
    _session_registry: Any
    _registry_row: Any
    _registered_session_id: str | None
    _caller_hook_session_id: str | None

    # --- Finalize-block locals ---
    _audit_outcome_to_finalize: Any  # AuditOutcome | None
    _semantic_path: Path | None
    _materialized: Any
    _materialized_status: Any  # AuditOutcomeStatus | None
    _timeout_exc: BaseException | None
    _timeout_result: Any
    _parsed: Any
    _missing: Any
    _shaped_response: str | None
    _replay_payload: dict | None
    _crashed_result: Any
    _unhandled_result: Any
    _cancelled_result: Any
    _completion_authority: Any
    _sid: str | None
    _ssm: Any
    _cleanup_dir: Path | None
    _codex_fallback: Any
