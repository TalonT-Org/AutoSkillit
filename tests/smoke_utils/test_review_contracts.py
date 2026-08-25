from __future__ import annotations

import pytest

from autoskillit.smoke_utils import (
    EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY,
    EXPERIMENTAL_REVIEW_AUDITORS,
    REVIEW_HANDOFF_IDENTITY_FIELDS,
    review_handoff_pair_error,
    select_experimental_review_dispatch,
)

pytestmark = [pytest.mark.medium]


def test_review_handoff_pair_requires_one_ordered_identity_contract() -> None:
    identity = {
        "_head_sha": "head",
        "annotation_generation_id": "annotation",
        "review_generation_id": "review",
    }

    assert REVIEW_HANDOFF_IDENTITY_FIELDS == tuple(identity)
    assert review_handoff_pair_error(identity, dict(identity)) is None


@pytest.mark.parametrize(
    ("field", "second_value", "expected_error"),
    [
        ("_head_sha", "", "require non-empty _head_sha"),
        (
            "annotation_generation_id",
            "different",
            "mismatched annotation_generation_id",
        ),
        ("review_generation_id", None, "require non-empty review_generation_id"),
    ],
)
def test_review_handoff_pair_rejects_missing_or_mixed_generations(
    field: str,
    second_value: object,
    expected_error: str,
) -> None:
    first = {
        "_head_sha": "head",
        "annotation_generation_id": "annotation",
        "review_generation_id": "review",
    }
    second = dict(first)
    second[field] = second_value

    assert expected_error in str(review_handoff_pair_error(first, second))


def test_experimental_dispatch_is_derived_from_ordered_registry() -> None:
    result = select_experimental_review_dispatch(
        gate_state="valid_true",
        annotated_diff="[L1] changed",
        valid_diff_lines={"src/app.py": [1]},
        standard_agent_names=["arch", "tests"],
    )

    expected = [
        {"agent_name": agent_name, "dimension": dimension}
        for agent_name, dimension in EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY
    ]
    assert EXPERIMENTAL_REVIEW_AUDITORS == tuple(item["agent_name"] for item in expected)
    assert result == {
        "audit_state": "pending",
        "dispatch_agents": expected,
        "auditors": expected,
        "reason": "",
    }


@pytest.mark.parametrize(
    ("gate_state", "annotated_diff", "valid_diff_lines", "standard_agents", "state"),
    [
        ("valid_false", "[L1] changed", {"src/app.py": [1]}, ["arch"], "not_required"),
        ("degraded", "[L1] changed", {"src/app.py": [1]}, ["arch"], "degraded"),
        ("valid_true", "", {"src/app.py": [1]}, ["arch"], "degraded"),
        ("valid_true", "[L1] changed", [], ["arch"], "degraded"),
        (
            "valid_true",
            "[L1] changed",
            {"src/app.py": [1]},
            [EXPERIMENTAL_REVIEW_AUDITORS[0]],
            "degraded",
        ),
    ],
)
def test_experimental_dispatch_closes_on_ineligible_authority(
    gate_state: str,
    annotated_diff: object,
    valid_diff_lines: object,
    standard_agents: list[str],
    state: str,
) -> None:
    result = select_experimental_review_dispatch(
        gate_state=gate_state,
        annotated_diff=annotated_diff,
        valid_diff_lines=valid_diff_lines,
        standard_agent_names=standard_agents,
    )

    assert result["audit_state"] == state
    assert result["dispatch_agents"] == []
