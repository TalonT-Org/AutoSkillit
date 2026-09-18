"""AdjudicationVerdict — structured explanation for a post-session success demotion.

Extracted from ``_type_results.py`` to keep that module under its line budget
while preserving the canonical-subtype registry defense and ``__post_init__``
validation as a single source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ._type_enums import RetryReason

# Canonical subtypes for AdjudicationVerdict, single-sourced here so a typo on
# one construction site fails fast instead of silently mismatching downstream
# literal comparisons. Kept as a frozenset rather than a Literal alias so
# AdjudicationVerdict.subtype stays a free-form ``str`` for ad-hoc extensions
# while still being validated at construction.
_ADJUDICATION_VERDICT_SUBTYPES: frozenset[str] = frozenset(
    {
        "artifact_adjudication_error",
        "artifact_contract_violation",
        "outcome_invariant_violation",
        "outcome_report_malformed",
        "test_evidence_missing",
        "test_evidence_stale",
        "tests_not_green",
        "zero_writes",
    }
)


@dataclass(frozen=True, slots=True)
class AdjudicationVerdict:
    """Structured explanation for a post-session success demotion."""

    reason_kind: RetryReason
    subtype: str
    detail: str
    outcome_fields: Mapping[str, int | str] | None
    defects: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.subtype:
            raise ValueError("AdjudicationVerdict.subtype must be a non-empty string")
        if not self.detail:
            raise ValueError("AdjudicationVerdict.detail must be a non-empty string")
        if self.subtype not in _ADJUDICATION_VERDICT_SUBTYPES:
            raise ValueError(
                f"AdjudicationVerdict.subtype {self.subtype!r} is not in the "
                f"canonical subtypes {sorted(_ADJUDICATION_VERDICT_SUBTYPES)}; "
                "extend the registry if the new failure mode is intentional."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "reason_kind": self.reason_kind.value,
            "subtype": self.subtype,
            "detail": self.detail,
            "outcome_fields": (
                dict(self.outcome_fields) if self.outcome_fields is not None else None
            ),
            "defects": list(self.defects),
        }


__all__ = ["AdjudicationVerdict"]
