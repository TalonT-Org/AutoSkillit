"""Backward-compat shim for path_containment — see core.io.path_containment."""

from autoskillit.core.io.path_containment import (
    ContainmentError,
    _open_beneath_root_without_symlinks,
    check_metadata_stable,
    read_stable_contained_bytes,
    read_stable_contained_range,
    resolve_contained_path,
)

__all__ = [
    "ContainmentError",
    "_open_beneath_root_without_symlinks",
    "check_metadata_stable",
    "read_stable_contained_bytes",
    "read_stable_contained_range",
    "resolve_contained_path",
]
