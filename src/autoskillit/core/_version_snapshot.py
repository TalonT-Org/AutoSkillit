"""Backward-compat shim for _version_snapshot — see core.io.version_snapshot."""

from autoskillit.core.io.version_snapshot import collect_version_snapshot

__all__ = ["collect_version_snapshot"]
