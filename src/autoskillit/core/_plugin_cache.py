"""Backward-compat facade for the plugin-cache lifecycle (issue #4741 decomposition).

The lifecycle concerns now live in three siblings:

- ``_retiring_cache`` — retiring-cache persistence + lock + mutation primitives
- ``_plugin_artifact_retirement`` — ``PluginArtifactRetirementEngine``
- ``_active_kitchens`` — active-kitchen registry + liveness

This module re-exports the public names those shards define so existing
importers continue to bind to ``autoskillit.core._plugin_cache``. The
``os``, ``psutil``, and ``shutil`` bindings are kept solely as monkeypatch
targets for tests that patch them through this facade path.
"""

from __future__ import annotations

import os  # noqa: F401 — re-exported for monkeypatch.setattr("autoskillit.core._plugin_cache.os", ...)
import shutil  # noqa: F401 — re-exported for monkeypatch.setattr("autoskillit.core._plugin_cache.shutil", ...)

import psutil  # noqa: F401 — re-exported for monkeypatch.setattr("autoskillit.core._plugin_cache.psutil", ...)

from ._active_kitchens import (  # noqa: F401 — re-exported
    ActiveKitchensReadResult,
    ActiveKitchensState,
    KitchenProcessIdentity,
    _active_kitchens_corrupt,
    _active_kitchens_lock,
    _active_kitchens_path,
    _check_pid_with_psutil,
    _identity_from_entry,
    _pid_alive,
    _read_active_kitchens_unlocked,
    any_kitchen_open,
    kitchen_entry_alive,
    read_active_kitchens_registry,
    register_active_kitchen,
    sample_kitchen_process_identity,
    unregister_active_kitchen,
)
from ._plugin_artifact_retirement import (  # noqa: F401 — re-exported
    PluginArtifactRetirementEngine,
)
from ._retiring_cache import (  # noqa: F401 — re-exported
    _RETIRING_CACHE_SCHEMA_VERSION,
    _classify_legacy_path,
    _install_lock_path,
    _InstallLock,
    _legacy_record_id,
    _next_corrupt_sidecar_path,
    _open_lock,
    _parse_utc,
    _record_from_json,
    _record_to_json,
    _retirement_intent,
    _retirement_staging_path,
    _retiring_cache_lock,
    _retiring_cache_path,
    _salvage_retiring_records,
    _write_retiring_cache_unlocked,
    append_retiring_record,
    due_retiring_records,
    is_reclaimable_artifact_path,
    migrate_retiring_cache_v1,
    read_retiring_cache,
    remove_retiring_records,
    repair_corrupt_retiring_cache,
)
from .io import write_versioned_json  # noqa: F401 — re-exported for tests/server/_helpers.py
