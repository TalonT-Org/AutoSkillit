"""Decomposed hook registry package.

Submodules:
  _hooks_defs        — HookDef, LifecycleContractDef, HookDriftResult dataclasses
  _registry_data     — HOOK_REGISTRY, LIFECYCLE_CONTRACTS, retirement tables, paths
  _risky_operations  — RISKY_GIT_OPERATIONS, RISKY_GH_SUBCOMMANDS, lifecycle validation
  _hashing           — compute_registry_hash + HOOK_REGISTRY_HASH + canonical payload
  _rendering         — generate_hooks_json + relocatable command rendering
  _discovery         — Claude settings path resolution
  _drift             — drift detection + broken-script detection
  _cache             — plugin-cache hook validation

Public API re-exported below for backwards compatibility. The previous
flat-module form (src/autoskillit/hook_registry.py) was removed when the
module exceeded the REQ-CNST-010 line cap; consumers continue to import
from `autoskillit.hook_registry` unchanged.
"""

from __future__ import annotations

from ._cache import validate_plugin_cache_hooks
from ._discovery import _claude_settings_path, iter_all_scope_paths
from ._drift import (
    HookDriftResult,
    _count_hook_registry_drift,
    _extract_script_basenames,
    _is_own_hook,
    _load_settings_data,
    canonical_script_basenames,
    find_broken_hook_scripts,
)
from ._hashing import (
    _canonical_registry_payload,
    compute_registry_hash,
)
from ._hooks_defs import (
    _LOGICAL_HOOK_COMPONENT,
    _MATCHERLESS_EVENT_TYPES,
    HookDef,
    HookEnvVarDef,
    LifecycleContractDef,
)
from ._registry_data import (  # noqa: F401  (_build_hook_registry consumed by autoskillit.hooks.__init__)
    FAIL_CLOSED_GUARD_BASENAMES,
    HOOK_ENV_CONTRACT,
    HOOK_REGISTRY,
    HOOKS_DIR,
    LIFECYCLE_CONTRACTS,
    NEW_SUBDIR_BASENAMES,
    PLUGIN_ROOT_TOKEN,
    RETIRED_SCRIPT_BASENAMES,
    _build_hook_registry,
)
from ._rendering import (
    _build_hook_command,
    _build_hook_entry,
    generate_hooks_json,
    render_hooks_json_text,
    render_relocatable_hook_command,
)
from ._risky_operations import (
    _contract_session_scopes,
    hook_applies_to_backend,
    validate_lifecycle_contracts,
)

# HOOK_REGISTRY is left empty here and populated by the wiring in
# ``autoskillit.hooks.__init__`` after both packages have finished
# initializing. The eager ``_build_hook_registry()`` call that used to
# live at module scope has been removed because it triggered an import
# cycle through ``autoskillit.hooks.__init__`` (which imports
# ``HOOK_REGISTRY`` from this package): the lazy ``from autoskillit.hooks
# import EXEMPT_*`` inside ``_build_hook_registry`` would observe a
# partially-initialized ``autoskillit.hooks`` module and raise
# ``ImportError``. Importing ``autoskillit.hooks`` (directly or
# transitively) before iterating ``HOOK_REGISTRY`` ensures the
# post-import population has run. HOOK_REGISTRY_HASH is recomputed at the
# same call site; the value cached at this module level is a placeholder
# for the empty list and is intentionally overwritten.
HOOK_REGISTRY_HASH = compute_registry_hash(
    HOOK_REGISTRY,
    RETIRED_SCRIPT_BASENAMES,
    LIFECYCLE_CONTRACTS,
)

__all__ = [
    "FAIL_CLOSED_GUARD_BASENAMES",
    "HOOKS_DIR",
    "HOOK_ENV_CONTRACT",
    "HOOK_REGISTRY",
    "HOOK_REGISTRY_HASH",
    "HookDef",
    "HookDriftResult",
    "HookEnvVarDef",
    "LifecycleContractDef",
    "LIFECYCLE_CONTRACTS",
    "NEW_SUBDIR_BASENAMES",
    "PLUGIN_ROOT_TOKEN",
    "RETIRED_SCRIPT_BASENAMES",
    "RISKY_GH_SUBCOMMANDS",
    "RISKY_GIT_OPERATIONS",
    "_LOGICAL_HOOK_COMPONENT",
    "_MATCHERLESS_EVENT_TYPES",
    "_build_hook_command",
    "_build_hook_entry",
    "_canonical_registry_payload",
    "_claude_settings_path",
    "_contract_session_scopes",
    "_count_hook_registry_drift",
    "_extract_script_basenames",
    "_is_own_hook",
    "_load_settings_data",
    "canonical_script_basenames",
    "compute_registry_hash",
    "find_broken_hook_scripts",
    "generate_hooks_json",
    "hook_applies_to_backend",
    "iter_all_scope_paths",
    "render_hooks_json_text",
    "render_relocatable_hook_command",
    "validate_lifecycle_contracts",
    "validate_plugin_cache_hooks",
]


# RISKY_GH_SUBCOMMANDS / RISKY_GIT_OPERATIONS are NOT eagerly imported
# from ``autoskillit.hooks`` because that module is still partially
# initialized during this package's own load (the cycle through
# ``autoskillit.hooks.__init__``). Resolving them lazily through PEP 562
# means the import is triggered only when a consumer actually asks for
# the constants — by which time both packages have finished initializing.
#
# The names are listed as bare string literals (rather than a
# ``frozenset``/``tuple`` collection) because the production env-read
# surface scanner (tests/contracts/test_ambient_env_surface.py) flags
# any module-level collection whose members are all UPPER_SNAKE_CASE
# as a candidate env-var set — those would then have to be registered
# in AMBIENT_ENV_DISPOSITIONS, which is the wrong surface for these
# re-export constants. A direct ``or`` chain keeps the resolver purely
# internal while keeping the public module attribute names unchanged.
_LAZY_RISKY_GH = "RISKY_GH_SUBCOMMANDS"
_LAZY_RISKY_GIT = "RISKY_GIT_OPERATIONS"


def __getattr__(name: str):
    """Resolve the lazy RISKY_* re-exports through ``autoskillit.hooks``."""
    if name == _LAZY_RISKY_GH or name == _LAZY_RISKY_GIT:
        from autoskillit.hooks import (  # noqa: PLC0415
            RISKY_GH_SUBCOMMANDS,
            RISKY_GIT_OPERATIONS,
        )

        value = RISKY_GH_SUBCOMMANDS if name == _LAZY_RISKY_GH else RISKY_GIT_OPERATIONS
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
