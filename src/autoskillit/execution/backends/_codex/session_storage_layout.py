"""Shared immutable layout contracts for private Codex session storage."""

from __future__ import annotations

import regex as re

_VIEW_ID_RE = re.compile(r"^[0-9a-f]{16}-[1-9][0-9]*$")
_LAUNCH_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_INDEX_READ_LIMIT = 4 * 1024 * 1024
_MANIFEST_READ_LIMIT = 256 * 1024
_MANIFEST_NAME = "manifest.json"
_LOCKS_SUBDIR = ".locks"
_INDEX_NAME = "codex-session-index.json"
_RECONCILIATION_AUDIT_SCHEMA_VERSION = 1
_MANIFEST_STATES = frozenset({"prepared", "running", "finalizing", "complete", "failed"})
_STORE_TO_PUBLIC = {"active": "sessions", "archived": "archived_sessions"}
_PUBLIC_TO_STORE = {value: key for key, value in _STORE_TO_PUBLIC.items()}
_SUPPORTED_LOCAL_FILESYSTEMS = frozenset(
    {
        "apfs",
        "bcachefs",
        "btrfs",
        "ext2",
        "ext3",
        "ext4",
        "f2fs",
        "hfs",
        "hfsplus",
        "overlay",
        "tmpfs",
        "xfs",
        "zfs",
    }
)
_INERT_NAMES = {
    "sessions": ".inert-sessions",
    "archived_sessions": ".inert-archived_sessions",
}
