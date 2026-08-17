"""FastMCP lifespan package for the AutoSkillit server.

Decomposed from a single ``_lifespan.py`` (869 lines) into four submodules
plus this facade:

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

# Side-effect imports: each submodule registers its own public names on import.
# The facade re-exports a curated subset for the rest of the server.
from autoskillit.server._lifespan import _lifespan as _lifespan_mod  # noqa: F401
from autoskillit.server._lifespan import _session_boots as _session_boots_mod  # noqa: F401
from autoskillit.server._lifespan import _startup_checks as _startup_checks_mod  # noqa: F401
from autoskillit.server._lifespan._lifespan import (
    _autoskillit_lifespan,
    _run_retiring_sweep_async,
)
from autoskillit.server._lifespan._session_boots import (
    _LIFESPAN_BOOT_REGISTRY,
    _cleanup_stale_loop,
    _fleet_auto_gate_boot,
    _food_truck_auto_gate_boot,
    _skill_auto_gate_boot,
)
from autoskillit.server._lifespan._startup_checks import (
    run_startup_drift_check,
    run_startup_hook_health_check,
)

__all__ = [
    "run_startup_drift_check",
    "run_startup_hook_health_check",
    "_autoskillit_lifespan",
    "_fleet_auto_gate_boot",
    "_food_truck_auto_gate_boot",
    "_skill_auto_gate_boot",
    "_LIFESPAN_BOOT_REGISTRY",
    "_cleanup_stale_loop",
    "_run_retiring_sweep_async",
]
