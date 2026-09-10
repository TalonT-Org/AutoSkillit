"""Backward-compat shim for _execution_marker — see core.pipeline._execution_marker."""

from autoskillit.core.pipeline._execution_marker import (
    _touch_marker,
    execution_marker,
)

__all__ = ["_touch_marker", "execution_marker"]
