"""Helpers shared by the MCP ``run_cmd``, ``run_python``, and ``run_skill`` tool surfaces.

Re-exports dispatch metadata, capture-stream plumbing, scalar coercion,
skill-contract lifecycle helpers, and the path-anchoring utilities that
``tools_execution.py`` and its tests bind through this package facade.
"""

from __future__ import annotations

from autoskillit.core import get_logger as _get_logger
from autoskillit.execution.process._process_io import summarize_capture  # noqa: F401
from autoskillit.recipe._contracts_types import SkillContract  # noqa: F401
from autoskillit.server.tools._execution_helpers._dispatch_metadata import (
    AuditOutputMode,
    aggregate_sandbox_overrides,
    bind_projection_backend,
    build_fresh_projection_context,
    build_validated_skill_dispatch_contract,
    check_review_approach_plan_path,
    compute_write_prefixes,
    derive_run_cmd_write_prefixes,
    invocation_member_names,
    resolve_skill_dispatch_metadata,
    resolve_step_name_from_recipe,
    scope_covers_cwd,
    select_audit_output_contract,
)
from autoskillit.server.tools._execution_helpers._run_cmd_spill import (
    _process_capture_stream,
    _spill_spec,
    _summarize_streams,
    _uuid8,
    propagate_session_deadline,
    run_cmd_artifact_root,
    spill_run_cmd_result,
)
from autoskillit.server.tools._execution_helpers._run_python_coercion import (
    _coerce_scalar,
    _import_and_call,
    maybe_promote_work_dir,
    resolve_relative_path_args,
    server_injected_run_python_args,
    shape_execution_response,
    validate_path_arg_anchoring,
)
from autoskillit.server.tools._execution_helpers._skill_contract import (
    _RunSkillContractLifecycle,
    build_skill_session_contract,
    clear_run_skill_state,
    deserialize_skill_contract,
    make_project_skill_resolver,
    persist_run_skill_state,
    rehydrate_skill_invocation,
    serialize_skill_contract,
    validate_resumed_skill_contract,
)

logger = _get_logger(__name__)

__all__ = [
    "AuditOutputMode",
    "SkillContract",
    "_RunSkillContractLifecycle",
    "_coerce_scalar",
    "_import_and_call",
    "_process_capture_stream",
    "_spill_spec",
    "_summarize_streams",
    "_uuid8",
    "aggregate_sandbox_overrides",
    "bind_projection_backend",
    "build_fresh_projection_context",
    "build_skill_session_contract",
    "build_validated_skill_dispatch_contract",
    "check_review_approach_plan_path",
    "clear_run_skill_state",
    "compute_write_prefixes",
    "derive_run_cmd_write_prefixes",
    "deserialize_skill_contract",
    "invocation_member_names",
    "logger",
    "make_project_skill_resolver",
    "maybe_promote_work_dir",
    "persist_run_skill_state",
    "propagate_session_deadline",
    "rehydrate_skill_invocation",
    "resolve_relative_path_args",
    "resolve_skill_dispatch_metadata",
    "resolve_step_name_from_recipe",
    "run_cmd_artifact_root",
    "scope_covers_cwd",
    "select_audit_output_contract",
    "serialize_skill_contract",
    "server_injected_run_python_args",
    "shape_execution_response",
    "spill_run_cmd_result",
    "summarize_capture",
    "validate_path_arg_anchoring",
    "validate_resumed_skill_contract",
]
