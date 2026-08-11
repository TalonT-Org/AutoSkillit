"""Shared relocatability test primitives.

The forbidden-segment list is the single authority for what constitutes an
environment-pinned path segment.  All relocatability tests consume it rather
than maintaining their own copies.  If a runtime validator ever needs the
list, promote it to ``core/types/_type_constants.py``.
"""

from __future__ import annotations

import sys

from autoskillit.core import pkg_root


def environment_pinned_path_segments() -> tuple[str, ...]:
    """Path segments that must never appear in a durable relocatable artifact.

    Returned as a function (not a module-level constant) because ``pkg_root()``
    and ``sys.prefix`` are process-specific and must be evaluated at call time,
    not at import time.
    """
    return (
        "site-packages",
        "/lib/python",
        "uv/tools",
        str(pkg_root()),
        sys.prefix,
    )
