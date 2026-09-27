"""Backward-compat shim. See core.context_admission.context_admission_indeterminate."""

from autoskillit.core.context_admission.context_admission_indeterminate import (
    _mark_indeterminate,
    _request_reconciliation,
    _resolve_indeterminate_accepted,
)

__all__ = [
    "_mark_indeterminate",
    "_request_reconciliation",
    "_resolve_indeterminate_accepted",
]
