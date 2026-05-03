"""Runtime subpackage: process-scoped state modules (stdlib-only foundation).

Re-exports the public surfaces of kitchen_state, readiness, session_registry,
and _linux_proc so callers can use ``from autoskillit.core.runtime import X``.
"""

from __future__ import annotations

from ._linux_proc import read_boot_id, read_starttime_ticks
from .kitchen_state import (
    KitchenMarker,
    get_state_dir,
    is_marker_fresh,
    marker_path,
    read_marker,
    sweep_stale_markers,
    write_marker,
)
from .readiness import (
    cleanup_readiness_sentinel,
    readiness_sentinel_path,
    write_readiness_sentinel,
)
from .session_registry import (
    bridge_claude_session_id,
    read_registry,
    registry_path,
    write_registry_entry,
)

__all__ = [
    "KitchenMarker",
    "bridge_claude_session_id",
    "cleanup_readiness_sentinel",
    "get_state_dir",
    "is_marker_fresh",
    "marker_path",
    "read_boot_id",
    "read_marker",
    "read_registry",
    "read_starttime_ticks",
    "readiness_sentinel_path",
    "registry_path",
    "sweep_stale_markers",
    "write_marker",
    "write_readiness_sentinel",
    "write_registry_entry",
]
