"""IL-0 pure reducer and coverage resolver for cumulative context admission.

Exposes the canonical public surface of the context-admission reducer,
match/case dispatcher, reducer registry contract, and coverage resolver
through the ``autoskillit.core.context_admission`` namespace. The per-event
dispatch-category shards live as sibling modules under this package and
are imported only by the gateway module ``context_admission``. Backward-compat
shims at ``core/context_admission.py`` and ``core/context_admission_*.py``
preserve old import paths after the core/context_admission/ decomposition.
"""

from __future__ import annotations

from ..types._type_enums import ProducerSurface
from .context_admission import (
    CONTEXT_ADMISSION_REDUCER_REGISTRY,
    ContextAdmissionReducerDef,
    ContextAdmissionValidationError,
    UnsupportedContextAdmissionProtocolError,
    context_admission_reducer_for_protocol,
    reduce_context_admission,
    replay_context_admission,
    resolve_context_admission_coverage,
)

__all__ = [
    "CONTEXT_ADMISSION_REDUCER_REGISTRY",
    "ContextAdmissionReducerDef",
    "ContextAdmissionValidationError",
    "ProducerSurface",
    "UnsupportedContextAdmissionProtocolError",
    "context_admission_reducer_for_protocol",
    "reduce_context_admission",
    "replay_context_admission",
    "resolve_context_admission_coverage",
]
