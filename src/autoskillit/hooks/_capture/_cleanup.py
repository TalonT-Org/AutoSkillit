"""Descriptor cleanup that preserves the authoritative failure."""

from __future__ import annotations

import os

from ._module_identity import register_module_aliases

register_module_aliases(__name__)


def close_preserving_primary(
    fd: int,
    primary_error: BaseException,
    *,
    context: str,
) -> None:
    """Close ``fd`` while retaining ``primary_error`` as the raised exception."""

    try:
        os.close(fd)
    except OSError as cleanup_error:
        primary_error.add_note(
            f"{context} also failed: {type(cleanup_error).__name__}: {cleanup_error}"
        )
