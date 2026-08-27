"""Durable, content-addressed quarantine markers for broken hook payloads."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from autoskillit.core import atomic_write


def hook_quarantine_marker_path(
    manifest_path: Path,
    raw_hooks: bytes,
) -> Path:
    """Return the external marker path for one exact hooks.json payload."""
    digest = sha256(raw_hooks).hexdigest()
    return manifest_path.parent / f"{manifest_path.name}.hook-quarantine-{digest}"


def is_hook_payload_quarantined(manifest_path: Path, raw_hooks: bytes) -> bool:
    """Return whether this exact hooks payload has a durable rejection marker."""
    return hook_quarantine_marker_path(manifest_path, raw_hooks).is_file()


def quarantine_hook_payload(manifest_path: Path, raw_hooks: bytes) -> Path:
    """Durably mark this exact hooks payload as terminally broken."""
    marker_path = hook_quarantine_marker_path(manifest_path, raw_hooks)
    if not marker_path.is_file():
        atomic_write(marker_path, "", strict_durability=True)
    return marker_path
