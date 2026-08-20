"""Structural guards for the tools_execution package facade (issue #4705).

T4 — every name a submodule reads through the facade at call time
(``_te_pkg.name(...)``, D5), or that ~70 existing tests patch directly via
``mock.patch("...tools_execution.<name>")``, must actually be a
``tools_execution`` attribute. A missing re-export makes a ``mock.patch`` a
silent no-op rather than an error, so nothing else catches it.

T5 — no submodule (other than ``__init__.py``) may bind a D5 symbol as a
module-level import. Doing so would create a private binding a
``mock.patch`` on the facade cannot reach, silently defeating the patch.

T6 — no submodule may call ``locals()``. D4 replaced every ``locals()``
frame lookup with explicit ``state`` reads; a reintroduced ``locals()``
call would silently reopen the unbound-vs-falsy ambiguity D4 closed.

Isolation: pure AST/attribute reads, no shared mutable state, no fixtures.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.server.tools import tools_execution

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_PKG_DIR = Path(tools_execution.__file__).parent

# The union of every name imported from, or patched on, tools_execution
# across the repo (issue #4705 plan, Step 4 re-export set).
_RE_EXPORTED_NAMES = (
    "run_cmd",
    "run_python",
    "run_skill",
    "INGREDIENT_LOCK_DENY_PREFIX",
    "DEPENDENCY_DENY_PREFIX",
    "_EXECUTION_TUNING_STEP_FIELDS",
    "_EXECUTION_TUNING_EXTERNALLY_RESOLVED",
    "_build_actual_mcp_kwargs",
    "_RunSkillDispatchState",
    "_ExplorerLaunchLease",
    "_PURE_SLEEP_RE",
    "_recipe_execution_deny",
    "_audit_preflight_step_names",
    "_audit_response",
    "_materialization_outcome_status",
    "_reject_missing_semantic_result",
    "_complete_resumed_audit",
    "_check_ingredient_locks",
    "_check_pipeline_deps",
    "_has_active_locks",
    "_completion_tracker_binding",
    "_begin_run_skill_completion",
    "_finalize_run_skill_completion",
    "_admit_recipe_execution",
    "_prepare_dispatch_backend",
    "_prepare_dispatch_session",
    "_execute_and_finalize_run_skill",
    "_record_explorer_launch_lease",
    "_mint_fresh_explorer_binding",
    "is_feature_enabled",
    "execution_marker",
    "read_registry",
    "get_tool_def",
    "AuditResultOutcome",
    "_notify",
    "progress_heartbeat",
    "_import_and_call",
    "shape_execution_response",
    "_run_subprocess_captured",
    "read_overlay",
    "resolve_closure_write_dirs",
    "_resolve_step_name_from_recipe",
    "_check_review_approach_plan_path",
    "_check_backend_compat",
    "_compute_write_prefixes",
    "_select_tracker_authority",
    "_RunSkillContractLifecycle",
    "_issue_explorer_binding_env",
    "_cleanup_explorer_launch",
    "_complete_audit_finalization_effects",
    "_release_context_tracker",
    "_explorer_launch_identity",
    "find_caller_session_id",
    "logger",
    "prepare_recipe_segment_delivery",
    "shutil",
)

# Symbols a mock.patch("...tools_execution.<name>", ...) must be able to
# intercept for every submodule caller — read via _te_pkg.name(...), never
# imported directly into a submodule's own namespace (D5).
_D5_SYMBOLS = frozenset(
    {
        "is_feature_enabled",
        "execution_marker",
        "_notify",
        "_import_and_call",
        "_run_subprocess_captured",
        "_resolve_step_name_from_recipe",
        "_check_review_approach_plan_path",
        "_check_ingredient_locks",
        "_check_pipeline_deps",
        "read_overlay",
        "resolve_closure_write_dirs",
        "progress_heartbeat",
        "read_registry",
        "get_tool_def",
        "AuditResultOutcome",
        "shape_execution_response",
        "_complete_audit_finalization_effects",
        "prepare_recipe_segment_delivery",
        "INGREDIENT_LOCK_DENY_PREFIX",
        "DEPENDENCY_DENY_PREFIX",
        "_explorer_launch_identity",
        "find_caller_session_id",
    }
)


def _submodule_paths() -> list[Path]:
    return sorted(p for p in _PKG_DIR.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("name", _RE_EXPORTED_NAMES)
def test_facade_reexports_every_cross_submodule_and_patched_name(name: str) -> None:
    assert hasattr(tools_execution, name), (
        f"tools_execution.{name} is missing — a mock.patch on this name, or a "
        f"_te_pkg.{name}(...) call site, would silently fail"
    )


@pytest.mark.parametrize("path", _submodule_paths(), ids=lambda p: p.name)
def test_submodules_do_not_import_d5_symbols_directly(path: Path) -> None:
    """No submodule may bind a D5 symbol via a module-level ImportFrom.

    A ``def``/assignment in the symbol's own defining submodule (e.g.
    ``_check_ingredient_locks`` in ``_gates.py``) is the origin, not an
    import, and is not flagged.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
    violations = bound & _D5_SYMBOLS
    assert not violations, (
        f"{path.name} imports D5 symbol(s) {sorted(violations)} directly — "
        f"read via _te_pkg.<name>(...) instead so mock.patch reaches it"
    )


@pytest.mark.parametrize("path", _submodule_paths(), ids=lambda p: p.name)
def test_submodules_do_not_call_locals(path: Path) -> None:
    """No submodule may call locals() — D4 replaced every frame lookup with
    explicit state reads, preserving unbound-vs-falsy semantics."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "locals"
    ]
    assert not violations, f"{path.name} calls locals() at line(s) {violations}"
