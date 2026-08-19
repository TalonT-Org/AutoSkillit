"""tools_kitchen package facade.

Decomposition of the legacy ``server.tools.tools_kitchen`` 2147-line module
into sibling submodules. The facade re-exports the six MCP tool/resource
entry points plus the internal helpers that tests pin via
``mock.patch("autoskillit.server.tools.tools_kitchen._name")`` so that the
patch reaches the call site regardless of which submodule actually
defines the function.

Submodule layout:
    _open_kitchen_transition   — transition lifecycle + ContextVar
    _open_kitchen_errors       — failure / validation envelope builders
    _hook_config               — hook-subprocess bridge config writer
    _tracker_authority         — tracker retain/release + auto-init
    _open_kitchen              — open_kitchen tool + handler + redisable + register
    _close_kitchen             — close_kitchen tool + handler
    _lock_ingredients          — lock_ingredients tool + overlay helpers
    _reload_session            — reload_session tool + sentinel writer
    _disable_quota_guard       — disable_quota_guard tool
    _get_recipe                — recipe:// resource + ingredient inspection
    _declare_join_batch        — declare_join_batch tool + join-ledger handler
"""

from __future__ import annotations

# Re-exports for tests that patch symbols via the package facade.
from autoskillit import __version__
from autoskillit.config import (
    build_config_authoritative_layer,
    build_config_default_layer,
    resolve_ingredient_defaults,
)
from autoskillit.core import (
    _collect_disabled_feature_tags,
    find_latest_session_id,
    initialize_kitchen_tracker,
    resolve_kitchen_id,
    try_retire_tracker,
    unregister_active_kitchen,
)
from autoskillit.core import get_logger as _get_logger
from autoskillit.execution import default_tether_dir, sweep_orphaned_tethers_async
from autoskillit.fleet import (
    discover_campaign_state_files,
    execute_dispatch,
    reap_stale_dispatches_async,
)
from autoskillit.hook_registry import iter_all_scope_paths
from autoskillit.pipeline import (
    bind_kitchen_intent,
    claim_kitchen_request,
    create_background_task,
    release_kitchen_request,
)
from autoskillit.server._guards import _require_orchestrator_exact
from autoskillit.server._misc import (
    _apply_triage_gate,
    _build_hook_diagnostic_warning,
    _hook_config_path,
    _prime_quota_cache,
    _quota_refresh_loop,
    resolve_log_dir,
)
from autoskillit.server._recipe_delivery import finalize_recipe_delivery
from autoskillit.server._recipe_execution import clear_recipe_execution
from autoskillit.server._recipe_segment_delivery import prepare_recipe_segment_delivery
from autoskillit.server.tools._overlay_state import locked_overlay, update_overlay
from autoskillit.server.tools._preflight import _check_dispatch_feasibility
from autoskillit.server.tools._serve_helpers import project_orchestrator_guidance, serve_recipe

# Tool entry points — each lives in its own submodule and is re-exported
# here so ``from autoskillit.server.tools.tools_kitchen import open_kitchen``
# continues to work for both end users and tests.
from autoskillit.server.tools.tools_kitchen._close_kitchen import (
    _close_kitchen_handler,
    close_kitchen,
)
from autoskillit.server.tools.tools_kitchen._declare_join_batch import (
    declare_join_batch,
)
from autoskillit.server.tools.tools_kitchen._disable_quota_guard import (
    disable_quota_guard,
)
from autoskillit.server.tools.tools_kitchen._get_recipe import (
    _build_tool_category_listing,
    _check_override_keys,
    _render_ingredients_only_response,
    get_recipe,
)
from autoskillit.server.tools.tools_kitchen._hook_config import (
    OutputBudgetPolicyHookPayload,
    QuotaGuardHookPayload,
    _output_budget_policy_hook_payload,
    _quota_guard_hook_payload,
    _update_hook_config_with_git_ops_policy,
    _update_hook_config_with_recipe,
    _write_hook_config,
)
from autoskillit.server.tools.tools_kitchen._lock_ingredients import (
    _apply_unlock_keys,
    _build_ingredient_key_suggestions,
    _compute_unlocked_steps,
    _write_ingredient_locks,
    lock_ingredients,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen import (
    _open_kitchen_handler,
    _redisable_subsets,
    open_kitchen,
)

# Internal helpers MUST be imported before _open_kitchen, because
# _open_kitchen.py imports its cross-submodule helpers (ContextVar,
# failure envelope, tracker authority, etc.) via this facade — and the
# facade cannot fully populate those names until their submodules are
# loaded.  Order: errors, transition, tracker_authority, hook_config,
# then the tool entry points.
from autoskillit.server.tools.tools_kitchen._open_kitchen_errors import (
    _kitchen_failure_envelope,
    _recipe_validation_error_response,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (
    _OPEN_KITCHEN_REQUEST_CTX,
    _attach_transition_fields,
    _bind_open_kitchen_transition,
    _ensure_kitchen_transition,
    _open_kitchen_cancellation_response,
    _open_kitchen_conflict_response,
    _read_open_kitchen_request_ctx,
    _transition_fields,
    _transition_start,
)
from autoskillit.server.tools.tools_kitchen._reload_session import (
    _find_session_id_for_reload,
    _reload_session_handler,
    _write_reload_sentinel,
    reload_session,
)
from autoskillit.server.tools.tools_kitchen._tracker_authority import (
    _auto_init_pipeline_tracker,
    _pipeline_tracker_auto_init_failure,
    _register_active_recipe_kitchen,
    _release_kitchen_tracker_authority,
    _retain_kitchen_tracker_authority,
    prune_stale_kitchen_state,
)

# Module-level logger kept at the facade so tests that
# ``monkeypatch.setattr("...tools_kitchen.logger", ...)`` rebind the same
# name the submodules use (they import ``logger`` from this facade).
logger = _get_logger(__name__)


def __getattr__(name: str):  # pragma: no cover — late-binding shim
    """Late-bind ``mcp`` from ``autoskillit.server`` for test patches."""
    if name == "mcp":
        import autoskillit.server as _server_pkg  # circular-break

        return _server_pkg.mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Public MCP entry points
    "open_kitchen",
    "close_kitchen",
    "declare_join_batch",
    "lock_ingredients",
    "reload_session",
    "disable_quota_guard",
    "get_recipe",
    # 12 internal helpers pinned by tests
    "_open_kitchen_handler",
    "_close_kitchen_handler",
    "_prime_quota_cache",
    "_redisable_subsets",
    "_write_hook_config",
    "_recipe_validation_error_response",
    "_kitchen_failure_envelope",
    "_quota_guard_hook_payload",
    "_output_budget_policy_hook_payload",
    "_auto_init_pipeline_tracker",
    "_reload_session_handler",
    "prune_stale_kitchen_state",
    "_OPEN_KITCHEN_REQUEST_CTX",
    # Additional internal helpers preserved for completeness
    "_apply_triage_gate",
    "_apply_unlock_keys",
    "_attach_transition_fields",
    "_bind_open_kitchen_transition",
    "_build_hook_diagnostic_warning",
    "_check_dispatch_feasibility",
    "_collect_disabled_feature_tags",
    "_ensure_kitchen_transition",
    "_find_session_id_for_reload",
    "_hook_config_path",
    "_quota_refresh_loop",
    "_read_open_kitchen_request_ctx",
    "_open_kitchen_cancellation_response",
    "_open_kitchen_conflict_response",
    "_transition_start",
    "_transition_fields",
    "_update_hook_config_with_recipe",
    "_update_hook_config_with_git_ops_policy",
    "_release_kitchen_tracker_authority",
    "_retain_kitchen_tracker_authority",
    "_register_active_recipe_kitchen",
    "_pipeline_tracker_auto_init_failure",
    "_require_orchestrator_exact",
    "_write_ingredient_locks",
    "_write_reload_sentinel",
    "__version__",
    "_compute_unlocked_steps",
    "_build_ingredient_key_suggestions",
    "_build_tool_category_listing",
    "_check_override_keys",
    "_render_ingredients_only_response",
    "OutputBudgetPolicyHookPayload",
    "QuotaGuardHookPayload",
    "bind_kitchen_intent",
    "build_config_authoritative_layer",
    "build_config_default_layer",
    "claim_kitchen_request",
    "clear_recipe_execution",
    "create_background_task",
    "default_tether_dir",
    "discover_campaign_state_files",
    "execute_dispatch",
    "finalize_recipe_delivery",
    "find_latest_session_id",
    "initialize_kitchen_tracker",
    "iter_all_scope_paths",
    "locked_overlay",
    "logger",
    "mcp",
    "prepare_recipe_segment_delivery",
    "project_orchestrator_guidance",
    "reap_stale_dispatches_async",
    "release_kitchen_request",
    "resolve_ingredient_defaults",
    "resolve_kitchen_id",
    "resolve_log_dir",
    "serve_recipe",
    "sweep_orphaned_tethers_async",
    "try_retire_tracker",
    "unregister_active_kitchen",
    "update_overlay",
]
