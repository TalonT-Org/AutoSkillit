"""Behavioral tests for immutable GitHub review anchor authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from autoskillit.core import (
    AdmittedAnchor,
    AnchorAdmission,
    AnchorAuthorityAvailability,
    DiffAnchorAuthority,
    GitHubReviewRequest,
    admit_anchor,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_REPOSITORY = "octo/example"
_HEAD_SHA = "a" * 40
_GENERATION_ID = "generation-1"


def _authority(
    *,
    right: Mapping[str, frozenset[int]] | None = None,
    left: Mapping[str, frozenset[int]] | None = None,
) -> DiffAnchorAuthority:
    return DiffAnchorAuthority.authoritative(
        repository=_REPOSITORY,
        pr_number=42,
        head_sha=_HEAD_SHA,
        generation_id=_GENERATION_ID,
        right_side_lines=right or {},
        left_side_lines=left or {},
    )


def test_authoritative_empty_map_rejects_every_anchor() -> None:
    authority = _authority()
    assert authority.availability is AnchorAuthorityAvailability.AUTHORITATIVE
    assert authority.classify("src/app.py", 1, "RIGHT") is (
        AnchorAdmission.REJECTED_PATH_NOT_IN_DIFF
    )


def test_unavailable_authority_is_not_permissive() -> None:
    authority = DiffAnchorAuthority.unavailable(
        repository=_REPOSITORY, pr_number=42, head_sha=_HEAD_SHA
    )
    assert authority.classify("src/app.py", 1, "RIGHT") is (
        AnchorAdmission.REJECTED_AUTHORITY_UNAVAILABLE
    )
    assert authority == DiffAnchorAuthority.from_wire(authority.to_wire())


def test_authority_rejects_head_sha_mismatch() -> None:
    with pytest.raises(ValueError, match="authority identity"):
        GitHubReviewRequest(
            repository=_REPOSITORY,
            pr_number=42,
            head_sha="b" * 40,
            logical_iteration="review-pr:1",
            anchor_authority=_authority(),
            event="COMMENT",
            body="Review",
        )


def test_request_repository_identity_is_case_insensitive() -> None:
    request = GitHubReviewRequest(
        repository="Octo/Example",
        pr_number=42,
        head_sha=_HEAD_SHA,
        logical_iteration="review-pr:1",
        anchor_authority=_authority(),
        event="COMMENT",
        body="Review",
    )
    assert request.anchor_authority.repository == _REPOSITORY


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"head_sha": "A" * 40}, "head_sha"),
        ({"right_side_lines": {"/absolute.py": frozenset({1})}}, "path"),
        ({"right_side_lines": {"src/../escape.py": frozenset({1})}}, "path"),
        ({"right_side_lines": {"src/app.py": frozenset({0})}}, "positive"),
        ({"right_side_lines": {"src/app.py": frozenset({True})}}, "positive"),
    ],
)
def test_authority_construction_rejects_malformed_inputs(
    overrides: dict[str, object], match: str
) -> None:
    values: dict[str, object] = {
        "repository": _REPOSITORY,
        "pr_number": 42,
        "head_sha": _HEAD_SHA,
        "generation_id": _GENERATION_ID,
        "right_side_lines": {},
        "left_side_lines": {},
    }
    values.update(overrides)
    with pytest.raises((TypeError, ValueError), match=match):
        DiffAnchorAuthority.authoritative(**values)  # type: ignore[arg-type]


@st.composite
def _nested_authorities(
    draw: Any,
) -> tuple[DiffAnchorAuthority, DiffAnchorAuthority, str, int, str]:
    side = draw(st.sampled_from(["LEFT", "RIGHT"]))
    superset = draw(st.frozensets(st.integers(min_value=1, max_value=50), max_size=20))
    subset = draw(st.frozensets(st.sampled_from(sorted(superset)))) if superset else frozenset()
    line = draw(st.integers(min_value=1, max_value=50))
    path = "src/property.py"
    small_lines = {path: subset}
    large_lines = {path: superset}
    if side == "RIGHT":
        small = _authority(right=small_lines)
        large = _authority(right=large_lines)
    else:
        small = _authority(left=small_lines)
        large = _authority(left=large_lines)
    return small, large, path, line, side


@given(_nested_authorities())
def test_admission_is_monotone_in_authority(
    case: tuple[DiffAnchorAuthority, DiffAnchorAuthority, str, int, str],
) -> None:
    subset, superset, path, line, side = case
    subset_result = subset.classify(path, line, side)
    superset_result = superset.classify(path, line, side)
    assert not (
        subset_result is AnchorAdmission.ADMITTED
        and superset_result is not AnchorAdmission.ADMITTED
    )


def test_left_side_anchor_validated_against_old_file_lines() -> None:
    authority = _authority(
        left={"src/app.py": frozenset({8, 9})},
        right={"src/app.py": frozenset({20, 21})},
    )
    admitted = admit_anchor(authority, "src/app.py", 9, "LEFT")
    assert isinstance(admitted, AdmittedAnchor)
    assert authority.classify("src/app.py", 20, "LEFT") is (
        AnchorAdmission.REJECTED_LINE_NOT_IN_DIFF
    )


def test_multiline_range_requires_both_endpoints_admitted() -> None:
    authority = _authority(right={"src/app.py": frozenset({10, 11, 12, 14})})
    assert isinstance(
        admit_anchor(authority, "src/app.py", 12, "RIGHT", 10, "RIGHT"),
        AdmittedAnchor,
    )
    assert authority.classify("src/app.py", 14, "RIGHT", 12, "RIGHT") is (
        AnchorAdmission.REJECTED_RANGE_NOT_CONTAINED
    )
    assert authority.classify("src/app.py", 12, "RIGHT", 10, "LEFT") is (
        AnchorAdmission.REJECTED_RANGE_NOT_CONTAINED
    )


def test_admitted_anchor_has_no_public_constructor_bypass() -> None:
    with pytest.raises(TypeError, match="only be created by admit_anchor"):
        AdmittedAnchor(
            authority_digest="f" * 64,
            path="src/app.py",
            line=1,
            side="RIGHT",
            start_line=None,
            start_side=None,
        )


def test_authority_defensively_freezes_maps_and_wire_shape_is_closed() -> None:
    source = {"src/app.py": {1}}
    authority = DiffAnchorAuthority.authoritative(
        repository=_REPOSITORY,
        pr_number=42,
        head_sha=_HEAD_SHA,
        generation_id=_GENERATION_ID,
        right_side_lines=source,
        left_side_lines={},
    )
    source["src/app.py"].add(2)
    assert authority.right_side_lines["src/app.py"] == frozenset({1})
    malformed = authority.to_wire()
    malformed["unknown"] = True
    with pytest.raises(ValueError, match="artifact shape"):
        DiffAnchorAuthority.from_wire(malformed)
    tampered = authority.to_wire()
    tampered["generation_id"] = "different"
    with pytest.raises(ValueError, match="authority_digest"):
        DiffAnchorAuthority.from_wire(tampered)
