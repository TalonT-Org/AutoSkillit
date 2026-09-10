"""Backward-compat shim. See core.context_admission.context_admission_propose_reserve."""

from autoskillit.core.context_admission.context_admission_propose_reserve import (  # noqa: F401
    _open_epoch,
    _preflight,
    _propose,
    _reserve,
)

__all__ = [
    "_open_epoch",
    "_preflight",
    "_propose",
    "_reserve",
]
