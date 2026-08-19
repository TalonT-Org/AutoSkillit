"""Index helpers for the managed headless session lineage store."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from autoskillit.core import (
    MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
    atomic_write,
)
from autoskillit.execution.session._managed_headless_session_lineage import (
    _INDEXES_DIR,
    ManagedHeadlessSessionLineageConflictError,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    _strict_str,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    canonical_json as _canonical_json,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    strict_json_load as _strict_json_load,
)
from autoskillit.execution.session._managed_headless_session_lineage_records import (
    _read_bounded,
)


def _index_path(root: Path, index_name: str, key: str) -> Path:
    if not isinstance(key, str) or not key or "\x00" in key:
        raise ValueError(f"Invalid managed lineage {index_name} key")
    if len(key.encode("utf-8")) > 512:
        raise ValueError(f"Managed lineage {index_name} key is oversized")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return root / _INDEXES_DIR / index_name / f"{digest}.json"


def _write_index(root: Path, index_name: str, key: str, launch_id: str) -> None:
    path = _index_path(root, index_name, key)
    atomic_write(
        path,
        _canonical_json(
            {
                "schema_version": MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION,
                "key": key,
                "launch_id": launch_id,
            }
        ),
        strict_durability=True,
    )


def _remove_index(root: Path, index_name: str, key: str) -> None:
    """Durably remove one index entry while its namespace lock is held."""
    path = _index_path(root, index_name, key)
    path.unlink()
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_index(root: Path, index_name: str, key: str) -> str:
    path = _index_path(root, index_name, key)
    value = _strict_json_load(_read_bounded(path))
    expected_fields = {"schema_version", "key", "launch_id"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError("Invalid managed lineage index")
    if value["schema_version"] != MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION:
        raise ValueError("Unsupported managed lineage index schema")
    if value["key"] != key:
        raise ValueError("Managed lineage index key mismatch")
    return _strict_str(value["launch_id"], "launch_id")


def _assert_index_available(
    root: Path,
    index_name: str,
    key: str,
    launch_id: str,
) -> None:
    path = _index_path(root, index_name, key)
    if not path.exists():
        return
    indexed_launch_id = _read_index(root, index_name, key)
    if indexed_launch_id != launch_id:
        raise ManagedHeadlessSessionLineageConflictError(
            f"Managed lineage {index_name} identity is already owned"
        )
