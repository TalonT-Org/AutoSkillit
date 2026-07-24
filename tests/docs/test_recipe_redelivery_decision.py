"""Ratchet the accepted recipe re-delivery decision record."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs/decisions/0004-recipe-redelivery.md"
DECISION_INDEX = REPO_ROOT / "docs/decisions/README.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]


@pytest.fixture(scope="module")
def decision_text() -> str:
    assert DECISION.exists(), "ADR-0004 must exist"
    return DECISION.read_text(encoding="utf-8")


def test_recipe_redelivery_decision_is_indexed(decision_text: str) -> None:
    assert "**Status:** Accepted" in decision_text
    assert "**Date:** 2026-06-27" in decision_text
    assert "0004-recipe-redelivery.md" in DECISION_INDEX.read_text(encoding="utf-8")


def test_decision_names_every_pullable_recipe_section(decision_text: str) -> None:
    for section in (
        "content",
        "ingredients_table",
        "orchestration_rules",
        "stop_step_semantics",
        "errors",
        "warnings",
    ):
        assert f"`{section}`" in decision_text
    assert "post-prune step" in decision_text


def test_decision_defines_all_reconstruction_algorithms(decision_text: str) -> None:
    for required in (
        "pagination_version",
        "section_registry_sha256",
        "section_sha256",
        "page_plan_sha256",
        "raw-text",
        "json-array-page",
        "json-scalar-page",
        "json-element-fragment",
        "json.loads",
        "element_sha256",
    ):
        assert required in decision_text


def test_decision_requires_fail_closed_continuation(decision_text: str) -> None:
    for required in (
        "unknown pagination version",
        "unknown content format",
        "gaps",
        "overlaps",
        "duplicates",
        "terminal",
        "next_part",
    ):
        assert required in decision_text.lower()
    assert "must not guess" in decision_text.lower()
