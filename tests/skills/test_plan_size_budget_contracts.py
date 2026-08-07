"""Contract: plan templates carry size_budget and digit-only rendering rule."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]


def _read_make_plan() -> str:
    return (pkg_root() / "skills_extended" / "make-plan" / "SKILL.md").read_text()


def _read_rectify() -> str:
    return (pkg_root() / "skills_extended" / "rectify" / "SKILL.md").read_text()


def test_make_plan_single_part_template_has_size_budget() -> None:
    content = _read_make_plan()
    # The single-part "Plan structure" template must contain a size_budget line.
    idx = content.find("Plan structure (single-part)")
    assert idx != -1
    section = content[idx : idx + 500]
    assert "size_budget = " in section


def test_make_plan_multi_part_template_has_size_budget() -> None:
    content = _read_make_plan()
    idx = content.find("Plan structure (multi-part")
    assert idx != -1
    section = content[idx : idx + 500]
    assert "size_budget = " in section


def test_make_plan_states_digit_only_rule() -> None:
    """The digit-only rendering rule must appear near size_budget guidance."""
    content = _read_make_plan()
    assert re.search(r"plain.digits", content), (
        "make-plan must state the digit-only rendering rule"
    )


def test_make_plan_instructs_per_step_estimates() -> None:
    content = _read_make_plan()
    assert re.search(r"estimated added.line count", content, re.IGNORECASE)


def test_make_plan_instructs_deferred_items() -> None:
    content = _read_make_plan()
    assert "Deferred Items" in content


def test_rectify_template_has_size_budget() -> None:
    content = _read_rectify()
    assert "size_budget = " in content


def test_rectify_has_proportionality_sentence() -> None:
    """Proportionality sentence must be within 3 lines of the maximalist mandate."""
    content = _read_rectify()
    lines = content.splitlines()
    mandate_idx = None
    proportionality_idx = None
    for i, line in enumerate(lines):
        if "solve more than just the issue at hand" in line:
            mandate_idx = i
        if "Immunity must be proportionate" in line:
            proportionality_idx = i
    assert mandate_idx is not None, "Maximalist mandate not found"
    assert proportionality_idx is not None, "Proportionality sentence not found"
    assert abs(proportionality_idx - mandate_idx) <= 3, (
        f"Proportionality sentence (line {proportionality_idx}) must be within "
        f"3 lines of the mandate (line {mandate_idx})"
    )
