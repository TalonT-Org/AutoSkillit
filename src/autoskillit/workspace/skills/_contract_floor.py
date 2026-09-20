"""Monotonic semantic admission for project-local skill overrides."""

from __future__ import annotations

from autoskillit.core import SkillInvalidityKind

from ._records import SkillInfo, SkillInvalidity


def contract_floor_invalidities(
    candidate: SkillInfo, bundled: SkillInfo | None
) -> tuple[SkillInvalidity, ...]:
    """Return invalidities when a local override weakens its bundled twin."""
    if bundled is None or bundled.semantic_plan is None:
        return ()

    bundled_plan = bundled.semantic_plan
    candidate_plan = candidate.semantic_plan
    weakened: list[str] = []
    if candidate_plan is None:
        weakened.append("semantic_requirements")
    else:
        if (
            bundled_plan.join is not None
            and bundled_plan.join.required
            and (candidate_plan.join is None or not candidate_plan.join.required)
        ):
            weakened.append("join.required")
        if (
            bundled_plan.concurrency is not None
            and bundled_plan.concurrency.required
            and (candidate_plan.concurrency is None or not candidate_plan.concurrency.required)
        ):
            weakened.append("concurrency.required")
        if (
            bundled_plan.evidence is not None
            and bundled_plan.evidence.required
            and (candidate_plan.evidence is None or not candidate_plan.evidence.required)
        ):
            weakened.append("evidence.required")
        if (
            bundled_plan.evidence is not None
            and bundled_plan.evidence.independent
            and (candidate_plan.evidence is None or not candidate_plan.evidence.independent)
        ):
            weakened.append("evidence.independent")

    if not weakened:
        return ()

    detail = f"project-local override weakens bundled semantic requirements: {', '.join(weakened)}"
    dropped_resources = sorted(set(bundled.required_resources) - set(candidate.required_resources))
    candidate_writes = candidate_plan.git_metadata_writes if candidate_plan is not None else ()
    dropped_writes = sorted(
        {write.purpose for write in bundled_plan.git_metadata_writes}
        - {write.purpose for write in candidate_writes}
    )
    # These fields describe functional needs; only weakened join, concurrency, and evidence reject.
    if dropped_resources:
        detail += f"; dropped requires_resources: {', '.join(dropped_resources)}"
    if dropped_writes:
        detail += f"; dropped git_metadata_writes: {', '.join(dropped_writes)}"
    return (
        SkillInvalidity(
            kind=SkillInvalidityKind.CONTRACT_FLOOR_WEAKENED,
            detail=detail,
        ),
    )


__all__ = ["contract_floor_invalidities"]
