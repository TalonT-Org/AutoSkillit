"""Session-invariant admission classifier.

Single source of truth for the `match adapt_session_invariant()` verdict
discrimination pattern that preflight, projected-artifact authority, and
the doctor standing-pin check all consume. Centralizing the support /
unsupported / launch-deferred discrimination prevents the three
sites from drifting out of sync on whether to fail closed or hand off
to the launch-scoped gate.

The classifier delegates the underlying `adapt_session_invariant` call
to ``_type_skill_semantics.adapt_session_invariant`` and re-exposes the
result with a third member, ``SessionInvariantAdaptationRefusal``, that
carries the unsupported operation+diagnostic pair out of the
opaque ``SkillSemanticAdaptationResult`` so callers no longer need to
check ``unsupported_operation is not None`` themselves.

Tracking: this module adds one file to ``core/types/`` and bumps
``FILE_COUNT_LIMITS``. A separate recipe-implementation ticket will
decompose the folder and lower the cap accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never, final

from ._type_skill_semantics import (
    LaunchEvidenceDeferral,
    SkillSemanticAdaptationResult,
    adapt_session_invariant,
)

if TYPE_CHECKING:
    from ._type_protocols_backend import CodingAgentBackend
    from ._type_skill_semantics import SkillSemanticOperation, SkillSemanticPlan


__all__ = [
    "SessionInvariantAdaptationRefusal",
    "SessionInvariantVerdict",
    "classify_session_invariant",
]


@final
@dataclass(frozen=True, slots=True)
class SessionInvariantAdaptationRefusal:
    """A backend's refusal that no launch evidence can lift."""

    operation: SkillSemanticOperation
    diagnostic: str

    def __post_init__(self) -> None:
        if not self.diagnostic:
            raise ValueError("SessionInvariantAdaptationRefusal.diagnostic must be non-empty")


SessionInvariantVerdict = (
    LaunchEvidenceDeferral | SessionInvariantAdaptationRefusal | SkillSemanticAdaptationResult
)


def classify_session_invariant(
    plan: SkillSemanticPlan,
    backend: CodingAgentBackend,
) -> SessionInvariantVerdict:
    """Classify *plan* under session-invariant admission.

    Returns one of three tagged verdicts:
    - ``LaunchEvidenceDeferral``: launch-bound managed-join evidence can lift the refusal
    - ``SessionInvariantAdaptationRefusal``: no launch evidence can lift the refusal
    - ``SkillSemanticAdaptationResult`` (without ``unsupported_operation``): backend supports

    Centralizes the match-on-union pattern that otherwise replicates across
    preflight, projection, and doctor call sites.
    """
    match adapt_session_invariant(plan, backend):
        case LaunchEvidenceDeferral() as deferral:
            return deferral
        case SkillSemanticAdaptationResult() as adaptation if (
            adaptation.unsupported_operation is not None
        ):
            if not adaptation.diagnostic:
                raise ValueError(
                    f"backend {backend.name!r} returned an unsupported refusal without "
                    "a diagnostic"
                )
            return SessionInvariantAdaptationRefusal(
                operation=adaptation.unsupported_operation,
                diagnostic=adaptation.diagnostic,
            )
        case SkillSemanticAdaptationResult() as adaptation:
            return adaptation
        case _ as unreachable:
            assert_never(unreachable)
