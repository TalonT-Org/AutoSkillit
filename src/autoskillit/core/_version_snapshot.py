"""Backward-compat shim for _version_snapshot — see core.io.version_snapshot."""

import subprocess  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.core._version_snapshot.subprocess", ...)

from autoskillit.core.io.version_snapshot import collect_version_snapshot

__all__ = ["collect_version_snapshot"]
