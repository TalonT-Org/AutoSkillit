"""Read the session deadline inherited by this process."""

from __future__ import annotations

import math
import os

__all__ = ["inherited_session_deadline_epoch"]


def inherited_session_deadline_epoch() -> float:
    """Return this process's inherited session deadline, or zero when invalid."""
    value = os.environ.get("AUTOSKILLIT_SESSION_DEADLINE")
    if value is None:
        return 0.0
    try:
        deadline = float(value)
    except ValueError:
        return 0.0
    if not math.isfinite(deadline) or deadline <= 0:
        return 0.0
    return deadline
