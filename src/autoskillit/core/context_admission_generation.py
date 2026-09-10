"""Backward-compat shim. See core.context_admission.context_admission_generation."""

from autoskillit.core.context_admission.context_admission_generation import (  # noqa: F401
    _mark_generation_indeterminate,
    _reconcile_closed_generation,
    _reconcile_generation,
    _start_generation,
)

__all__ = [
    "_mark_generation_indeterminate",
    "_reconcile_closed_generation",
    "_reconcile_generation",
    "_start_generation",
]
