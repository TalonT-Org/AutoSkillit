"""Backward-compat shim. See core.context_admission.context_admission_accept_release."""

from autoskillit.core.context_admission.context_admission_accept_release import (  # noqa: F401
    _accept,
    _accept_closed_input,
    _release_closed_batch,
    _release_or_rollback,
)

__all__ = [
    "_accept",
    "_accept_closed_input",
    "_release_closed_batch",
    "_release_or_rollback",
]
