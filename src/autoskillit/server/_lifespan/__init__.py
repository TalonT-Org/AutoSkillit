"""FastMCP lifespan package for the AutoSkillit server.

Provides the async context manager wired into FastMCP via ``lifespan=``, the
per-session auto-gate boot dispatch that runs before the lifespan yields (so
gate state is set before the first tool call arrives), and the one-shot
startup checks:

- :mod:`_startup_checks` — one-shot synchronous startup checks
  (``run_startup_drift_check``, ``run_startup_hook_health_check``,
  ``run_startup_install_state_check``, ``run_startup_fix_required_coverage_check``,
  ``_activate_recipe_kitchen``, ``_retain_context_tracker_authority``,
  ``_finalize_recorder``).
- :mod:`_session_boots` — per-session-type async auto-gate boots and the
  ``_LIFESPAN_BOOT_REGISTRY`` dispatch table.
- :mod:`_lifespan` — FastMCP lifespan glue: the ``_autoskillit_lifespan``
  async context manager plus the async wrappers that offload blocking checks
  to executor threads.
"""

from __future__ import annotations

import asyncio as _asyncio  # noqa: F401  (mock.patch reachability)

import autoskillit.core.paths as _core_paths  # noqa: F401  (mock.patch reachability)
from autoskillit.core import (  # noqa: F401  (mock.patch reachability)
    _collect_disabled_feature_tags,
    cleanup_readiness_sentinel,
    register_active_kitchen,
    resolve_kitchen_id,
    write_readiness_sentinel,
)
from autoskillit.execution import (  # noqa: F401  (mock.patch reachability)
    default_tether_dir,
    sweep_orphaned_tethers_async,
)
from autoskillit.fleet import (  # noqa: F401  (mock.patch reachability)
    discover_campaign_state_files,
    reap_stale_dispatches_async,
    sweep_stale_dispatch_labels,
)
from autoskillit.hook_registry import (  # noqa: F401  (mock.patch reachability)
    iter_all_scope_paths,
    validate_plugin_cache_hooks,
)
from autoskillit.pipeline import create_background_task  # noqa: F401  (mock.patch reachability)
from autoskillit.server._lifespan._lifespan import (
    _autoskillit_lifespan,
    _run_backend_mcp_registration_async,
    _run_deferred_init,
    _run_drift_check_async,
    _run_hook_health_check_async,
    _run_install_state_check_async,
    _run_lifespan_session_boot,
    _run_retiring_sweep_async,
)
from autoskillit.server._lifespan._session_boots import (
    _LIFESPAN_BOOT_REGISTRY,
    _cleanup_stale_loop,
    _evidence_reader_auto_gate_boot,
    _explorer_auto_gate_boot,
    _fleet_auto_gate_boot,
    _food_truck_auto_gate_boot,
    _pre_reveal_kitchen,
    _reap_self_excluded_codex_and_daemon_orphans,
    _skill_auto_gate_boot,
)
from autoskillit.server._lifespan._startup_checks import (
    _activate_recipe_kitchen,
    _finalize_recorder,
    _retain_context_tracker_authority,
    run_startup_drift_check,
    run_startup_fix_required_coverage_check,
    run_startup_hook_health_check,
    run_startup_install_state_check,
)
from autoskillit.server._state import _get_ctx_or_none  # noqa: F401  (mock.patch reachability)
from autoskillit.workspace import repair_broken_plugin_cache_hooks  # noqa: F401

__all__ = [
    "_LIFESPAN_BOOT_REGISTRY",
    "_activate_recipe_kitchen",
    "_autoskillit_lifespan",
    "_cleanup_stale_loop",
    "_evidence_reader_auto_gate_boot",
    "_explorer_auto_gate_boot",
    "_finalize_recorder",
    "_fleet_auto_gate_boot",
    "_food_truck_auto_gate_boot",
    "_pre_reveal_kitchen",
    "_reap_self_excluded_codex_and_daemon_orphans",
    "_retain_context_tracker_authority",
    "_run_backend_mcp_registration_async",
    "_run_deferred_init",
    "_run_drift_check_async",
    "_run_hook_health_check_async",
    "_run_install_state_check_async",
    "_run_lifespan_session_boot",
    "_run_retiring_sweep_async",
    "_skill_auto_gate_boot",
    "default_tether_dir",
    "run_startup_drift_check",
    "run_startup_fix_required_coverage_check",
    "run_startup_hook_health_check",
    "run_startup_install_state_check",
    "sweep_orphaned_tethers_async",
]
