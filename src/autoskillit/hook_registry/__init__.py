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
    LifecycleContractDef,
)
from ._registry_data import (
    FAIL_CLOSED_GUARD_BASENAMES,
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
    RISKY_GH_SUBCOMMANDS,
    RISKY_GIT_OPERATIONS,
    _contract_session_scopes,
    hook_applies_to_backend,
    validate_lifecycle_contracts,
)

# Defer HOOK_REGISTRY construction until every other module-level binding
# is in place. ``_build_hook_registry()`` imports
# ``autoskillit.hooks._hook_constants`` lazily; the cycle through
# ``autoskillit.hooks.__init__.py`` is broken by importing it after this
# package is fully constructed.
HOOK_REGISTRY.extend(_build_hook_registry())
# HOOK_REGISTRY_HASH is computed against the now-populated HOOK_REGISTRY.
# Recompute here because the empty-list value cached at import time is stale.
HOOK_REGISTRY_HASH = compute_registry_hash(
    HOOK_REGISTRY,
    RETIRED_SCRIPT_BASENAMES,
    LIFECYCLE_CONTRACTS,
)

__all__ = [
    "FAIL_CLOSED_GUARD_BASENAMES",
    "HOOKS_DIR",
    "HOOK_REGISTRY",
    "HOOK_REGISTRY_HASH",
    "HookDef",
    "HookDriftResult",
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
