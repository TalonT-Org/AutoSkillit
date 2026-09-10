"""Backward-compat shim. See core.context_admission.context_admission_expiry_rollover."""

from autoskillit.core.context_admission.context_admission_expiry_rollover import (  # noqa: F401
    _expire_idempotency,
    _rollover,
)

__all__ = [
    "_expire_idempotency",
    "_rollover",
]
