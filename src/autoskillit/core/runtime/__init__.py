"""Runtime subpackage: process-scoped state modules (stdlib-only foundation).

Re-exports the public surfaces of kitchen_state, readiness, session_registry,
and _linux_proc so callers can use ``from autoskillit.core.runtime import X``.
"""

from __future__ import annotations

from ._linux_proc import is_session_alive, read_boot_id, read_starttime_ticks
from .artifact_lease import ArtifactLease, ArtifactLeaseContention
from .executable_binding import (
    executable_binding_matches_current_file,
    resolve_executable_launch_binding,
)
from .kitchen_state import (
    KitchenMarker,
    find_caller_session_id,
    get_state_dir,
    is_marker_fresh,
    marker_path,
    read_kitchen_id_from_marker,
    read_marker,
    resolve_kitchen_id,
    sweep_stale_markers,
    write_marker,
)
from .private_file import (
    PrivateFileIdentity,
    PrivateSidecarIssue,
    fsync_directory,
    fsync_file,
    private_file_identity,
    private_sidecar_issue,
    publish_private_file,
    reconcile_initialization_links,
    unlink_sqlite_initialization_artifacts,
)
from .readiness import (
    cleanup_readiness_sentinel,
    readiness_sentinel_path,
    write_readiness_sentinel,
)
from .session_provenance import (
    ProvenanceRecord,
    provenance_path,
    read_provenance_for_session,
    write_provenance_record,
)
from .session_registry import (
    bridge_claude_session_id,
    read_registry,
    registry_path,
    write_registry_entry,
)

__all__ = [
    "ArtifactLease",
    "ArtifactLeaseContention",
    "PrivateFileIdentity",
    "PrivateSidecarIssue",
    "executable_binding_matches_current_file",
    "fsync_directory",
    "fsync_file",
    "is_session_alive",
    "KitchenMarker",
    "bridge_claude_session_id",
    "cleanup_readiness_sentinel",
    "find_caller_session_id",
    "get_state_dir",
    "is_marker_fresh",
    "marker_path",
    "private_file_identity",
    "private_sidecar_issue",
    "publish_private_file",
    "provenance_path",
    "read_boot_id",
    "read_kitchen_id_from_marker",
    "read_marker",
    "resolve_kitchen_id",
    "read_provenance_for_session",
    "read_registry",
    "read_starttime_ticks",
    "readiness_sentinel_path",
    "resolve_executable_launch_binding",
    "reconcile_initialization_links",
    "registry_path",
    "sweep_stale_markers",
    "unlink_sqlite_initialization_artifacts",
    "write_marker",
    "write_provenance_record",
    "write_readiness_sentinel",
    "write_registry_entry",
    "ProvenanceRecord",
]
