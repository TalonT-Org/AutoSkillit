"""Shared executable contracts for review artifact handoffs and auditor dispatch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY = (
    ("pr-review-auditor-reachability", "overengineering_reachability"),
    (
        "pr-review-auditor-abstraction-surface",
        "overengineering_abstraction_surface",
    ),
)
EXPERIMENTAL_REVIEW_AUDITORS = tuple(
    auditor_name for auditor_name, _dimension in EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY
)
REVIEW_HANDOFF_IDENTITY_FIELDS = (
    "_head_sha",
    "annotation_generation_id",
    "review_generation_id",
)


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _closed_key_set_error(
    value: object,
    *,
    expected: set[str],
    subject: str,
) -> str | None:
    if not isinstance(value, dict):
        return f"{subject} must be an object"
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(str(key) for key in actual - expected)
    if missing or extra:
        return f"{subject} has invalid closed keys: missing={missing}; extra={extra}"
    return None


def review_handoff_pair_error(
    first: object,
    second: object,
) -> str | None:
    """Return a deterministic error when paired review artifacts have mixed identities."""
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return "paired review artifacts must both be objects"
    for field in REVIEW_HANDOFF_IDENTITY_FIELDS:
        first_value = first.get(field)
        second_value = second.get(field)
        if not _is_non_empty_string(first_value) or not _is_non_empty_string(second_value):
            return f"paired review artifacts require non-empty {field}"
        if first_value != second_value:
            return f"paired review artifacts have mismatched {field}"
    return None


def select_experimental_review_dispatch(
    *,
    gate_state: str,
    annotated_diff: object,
    valid_diff_lines: object,
    standard_agent_names: Sequence[str],
) -> dict[str, object]:
    """Return the structured proof-auditor dispatch without duplicating its registry."""
    auditors = [
        {"agent_name": agent_name, "dimension": dimension}
        for agent_name, dimension in EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY
    ]
    overlap = sorted(set(standard_agent_names) & set(EXPERIMENTAL_REVIEW_AUDITORS))
    if gate_state != "valid_true":
        return {
            "audit_state": "not_required" if gate_state == "valid_false" else "degraded",
            "dispatch_agents": [],
            "auditors": auditors,
            "reason": "gate_false" if gate_state == "valid_false" else "gate_degraded",
        }
    if overlap:
        return {
            "audit_state": "degraded",
            "dispatch_agents": [],
            "auditors": auditors,
            "reason": f"standard_allowlist_overlap:{','.join(overlap)}",
        }
    if not _is_non_empty_string(annotated_diff) or not isinstance(valid_diff_lines, Mapping):
        return {
            "audit_state": "degraded",
            "dispatch_agents": [],
            "auditors": auditors,
            "reason": "missing_dispatch_authority",
        }
    return {
        "audit_state": "pending",
        "dispatch_agents": auditors,
        "auditors": auditors,
        "reason": "",
    }
