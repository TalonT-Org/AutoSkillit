"""Backward-compat shim for _step_context — see core.pipeline._step_context."""

from autoskillit.core.pipeline._step_context import (
    current_order_id,
    current_step_name,
)

__all__ = ["current_order_id", "current_step_name"]
