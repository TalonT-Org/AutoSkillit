"""Shared relocatability test primitives."""

from __future__ import annotations

import sys

from autoskillit.core import pkg_root


def environment_pinned_path_segments() -> tuple[str, ...]:
    """Return path segments forbidden in durable relocatable artifacts."""
    return (
        "site-packages",
        "/lib/python",
        "uv/tools",
        str(pkg_root()),
        sys.prefix,
    )
