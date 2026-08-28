"""MCP tool handlers: run_cmd, run_python, run_skill.

Facade package. Every name a submodule reads cross-submodule, and every name
roughly 70 existing tests patch via ``mock.patch("...tools_execution.<name>")``,
is re-exported here. Submodules bind the package object
(``from autoskillit.server.tools import tools_execution as _te_pkg``) and call
``_te_pkg.name(...)`` at call time rather than importing directly, so a patch
applied to this module's namespace reaches every submodule (D5 in the #4705
implementation plan). Layout:

- ``_state.py`` — ``_RunSkillDispatchState``, the dataclass threaded through
  every ``run_skill`` dispatch phase.
- ``_gates.py`` — ingredient-lock / pipeline-dependency preflight gates, and
  completion-receipt begin/finalize.
- ``_audit_response.py`` — audit outcome JSON rendering and resumed-audit
  completion.
- ``_run_skill_admission.py`` / ``_run_skill_prepare.py`` /
  ``_run_skill_session.py`` / ``_run_skill_finalize.py`` — the four
  ``run_skill`` dispatch phases.
- ``_run_skill_dispatch.py`` — the ``run_skill`` MCP tool itself; owns the
  outer ``try``/``except``/``finally`` and calls each phase in turn.
- ``_run_cmd.py`` / ``_run_python.py`` — the ``run_cmd`` and ``run_python``
  MCP tools.
"""

from __future__ import annotations

import shutil  # noqa: F401 — mock.patch("...tools_execution.shutil.which") resolves by

# attribute lookup (tools_execution.shutil) before reaching .which, so the facade
# needs its own shutil attribute even though it's a shared stdlib module object.
from autoskillit.core import (
    EXECUTION_TUNING_EXTERNALLY_RESOLVED,  # noqa: F401
    EXECUTION_TUNING_STEP_FIELDS,  # noqa: F401
    AuditResultOutcome,  # noqa: F401
    execution_marker,  # noqa: F401
    find_caller_session_id,  # noqa: F401
    get_logger,
    get_tool_def,  # noqa: F401
    is_feature_enabled,  # noqa: F401
    read_registry,  # noqa: F401
)
from autoskillit.server._explorer_projection import (
    _cleanup_explorer_launch,  # noqa: F401
    _explorer_launch_identity,  # noqa: F401
    _issue_explorer_binding_env,  # noqa: F401
)
from autoskillit.server._misc import (
    resolve_closure_write_dirs,  # noqa: F401
)
from autoskillit.server._notify import _notify  # noqa: F401
from autoskillit.server._progress_heartbeat import (
    progress_heartbeat,  # noqa: F401
)
from autoskillit.server._recipe_execution import (
    complete_audit_finalization_effects as _complete_audit_finalization_effects,  # noqa: F401
)
from autoskillit.server._recipe_segment_delivery import (
    prepare_recipe_segment_delivery,  # noqa: F401
)
from autoskillit.server._subprocess import (
    _run_subprocess_captured,  # noqa: F401
)
from autoskillit.server.tools._backend_compat import (
    _check_backend_compat,  # noqa: F401
)
from autoskillit.server.tools._execution_helpers import (
    _import_and_call,  # noqa: F401
    _RunSkillContractLifecycle,  # noqa: F401 — default_factory source; also re-exported
    shape_execution_response,  # noqa: F401
)
from autoskillit.server.tools._execution_helpers import (
    check_review_approach_plan_path as _check_review_approach_plan_path,  # noqa: F401
)
from autoskillit.server.tools._execution_helpers import (
    compute_write_prefixes as _compute_write_prefixes,  # noqa: F401
)
from autoskillit.server.tools._execution_helpers import (
    resolve_step_name_from_recipe as _resolve_step_name_from_recipe,  # noqa: F401
)
from autoskillit.server.tools._overlay_state import (
    read_overlay,  # noqa: F401
)
from autoskillit.server.tools.tools_pipeline_tracker import (
    _release_context_tracker,  # noqa: F401 — re-exported for facade completeness
    _select_tracker_authority,  # noqa: F401 — re-exported for facade completeness
)
from autoskillit.workspace import (  # noqa: F401
    create_git_worktree,
    remove_git_worktree,
)

from ._audit_response import (  # noqa: F401
    _audit_response,
    _complete_resumed_audit,
    _materialization_outcome_status,
    _reject_missing_semantic_result,
)
from ._fixed_batch_handlers import (  # noqa: F401
    read_fixed_batch_result,
    run_fixed_batch,
)
from ._gates import (  # noqa: F401
    DEPENDENCY_DENY_PREFIX,
    INGREDIENT_LOCK_DENY_PREFIX,
    _begin_run_skill_completion,
    _check_ingredient_locks,
    _check_pipeline_deps,
    _completion_tracker_binding,
    _finalize_run_skill_completion,
    _has_active_locks,
)
from ._managed_leaf import scoped_child_resource_owner  # noqa: F401
from ._run_cmd import _PURE_SLEEP_RE, run_cmd  # noqa: F401
from ._run_python import run_python  # noqa: F401
from ._run_skill_admission import (  # noqa: F401
    _admit_recipe_execution,
    _audit_preflight_step_names,
    _build_actual_mcp_kwargs,
    _recipe_execution_deny,
)
from ._run_skill_dispatch import run_skill  # noqa: F401
from ._run_skill_finalize import _execute_and_finalize_run_skill  # noqa: F401
from ._run_skill_prepare import (  # noqa: F401
    _ExplorerLaunchLease,
    _prepare_dispatch_backend,
    _record_explorer_launch_lease,
)
from ._run_skill_session import (  # noqa: F401
    _mint_fresh_explorer_binding,
    _prepare_dispatch_session,
)
from ._state import _RunSkillDispatchState  # noqa: F401

logger = get_logger(__name__)

__all__ = [
    "DEPENDENCY_DENY_PREFIX",
    "EXECUTION_TUNING_EXTERNALLY_RESOLVED",
    "EXECUTION_TUNING_STEP_FIELDS",
    "INGREDIENT_LOCK_DENY_PREFIX",
    "AuditResultOutcome",
    "_ExplorerLaunchLease",
    "_PURE_SLEEP_RE",
    "_RunSkillContractLifecycle",
    "_RunSkillDispatchState",
    "_admit_recipe_execution",
    "_audit_preflight_step_names",
    "_audit_response",
    "_begin_run_skill_completion",
    "_build_actual_mcp_kwargs",
    "_check_backend_compat",
    "_check_ingredient_locks",
    "_check_pipeline_deps",
    "_check_review_approach_plan_path",
    "_cleanup_explorer_launch",
    "_compute_write_prefixes",
    "_complete_audit_finalization_effects",
    "_complete_resumed_audit",
    "_completion_tracker_binding",
    "_execute_and_finalize_run_skill",
    "_explorer_launch_identity",
    "_finalize_run_skill_completion",
    "_has_active_locks",
    "_import_and_call",
    "_issue_explorer_binding_env",
    "_materialization_outcome_status",
    "_mint_fresh_explorer_binding",
    "_notify",
    "_prepare_dispatch_backend",
    "_prepare_dispatch_session",
    "_recipe_execution_deny",
    "_record_explorer_launch_lease",
    "_reject_missing_semantic_result",
    "_release_context_tracker",
    "_resolve_step_name_from_recipe",
    "_run_subprocess_captured",
    "_select_tracker_authority",
    "scoped_child_resource_owner",
    "create_git_worktree",
    "execution_marker",
    "find_caller_session_id",
    "get_tool_def",
    "is_feature_enabled",
    "logger",
    "prepare_recipe_segment_delivery",
    "progress_heartbeat",
    "read_overlay",
    "remove_git_worktree",
    "read_fixed_batch_result",
    "read_registry",
    "resolve_closure_write_dirs",
    "run_cmd",
    "run_fixed_batch",
    "run_python",
    "run_skill",
    "shape_execution_response",
    "shutil",
]
