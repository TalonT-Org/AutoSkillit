"""Backward-compat shim. See core.context_admission.context_admission_prepare_stage_dispatch."""

from autoskillit.core.context_admission.context_admission_prepare_stage_dispatch import (  # noqa: F401
    _dispatch,
    _prepare,
    _stage,
)

__all__ = [
    "_dispatch",
    "_prepare",
    "_stage",
]
