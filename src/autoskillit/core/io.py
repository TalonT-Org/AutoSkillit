"""Backward-compat shim for ``core/io.py`` -- see ``core.io.io``.

Real implementation lives at ``autoskillit.core.io.io`` after
issue #4671 Phase A. This shim preserves the old import path
``autoskillit.core.io`` for downstream callers.
"""

from __future__ import annotations

from autoskillit.core.io.io import (
    ReadResult,
    TreeVanishedError,
    atomic_write,
    decode_versioned_json_bytes,
    ensure_project_temp,
    is_python_bytecode_path,
    read_versioned_json,
    resolve_skill_temp_dir,
    resolve_temp_dir,
    safe_upsert_section,
    spill_output,
    strict_walk,
    temp_dir_display_str,
    write_canonical_versioned_json,
    write_versioned_json,
)

__all__ = [
    "ReadResult",
    "TreeVanishedError",
    "atomic_write",
    "decode_versioned_json_bytes",
    "ensure_project_temp",
    "is_python_bytecode_path",
    "read_versioned_json",
    "resolve_skill_temp_dir",
    "resolve_temp_dir",
    "safe_upsert_section",
    "spill_output",
    "strict_walk",
    "temp_dir_display_str",
    "write_canonical_versioned_json",
    "write_versioned_json",
]
