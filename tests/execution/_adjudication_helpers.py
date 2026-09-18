"""Shared adjudication test helpers.

Promoted from the per-module ``_assert_demotion_verdict`` helpers in
``test_finding_dispositions.py`` and ``test_outcome_invariants.py`` so a
doctrinal change to the demotion-verdict contract lands once.
"""

from __future__ import annotations

from autoskillit.core import SkillResult

__all__ = ["assert_demotion_verdict"]


def assert_demotion_verdict(
    result: SkillResult,
    *,
    outcome_fields: object = ...,
    defects: tuple[str, ...] = (),
) -> None:
    """Assert a reconciliation demotion retains one causal verdict.

    The verdict must agree with the SkillResult on reason_kind, subtype,
    and detail (carried as result.result). Pass ``outcome_fields`` to
    assert a specific expected value on both ``result.outcome_fields`` and
    ``verdict.outcome_fields``; omit it (or pass the sentinel ``...``) to
    only assert that the two agree with each other. ``defects`` is
    caller-controlled because different failure paths emit different defects.
    """
    verdict = result.adjudication_verdict

    assert verdict is not None
    assert verdict.reason_kind is result.retry_reason
    assert verdict.subtype == result.subtype
    assert result.result == verdict.detail
    if outcome_fields is ...:
        assert verdict.outcome_fields == result.outcome_fields
    else:
        assert result.outcome_fields == outcome_fields
        assert verdict.outcome_fields == outcome_fields
    assert verdict.defects == defects
