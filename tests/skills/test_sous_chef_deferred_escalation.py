"""Contract tests for sous-chef deferred issue escalation (T6/T7)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def _sous_chef_text() -> str:
    return (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "skills"
        / "sous-chef"
        / "SKILL.md"
    ).read_text()


def test_sous_chef_skillmd_has_deferred_escalation() -> None:
    content = _sous_chef_text()
    assert "deferred_groups" in content
    assert "gated_by" in content
    assert "AskUserQuestion" in content
    assert "Wait" in content and "Proceed" in content and "Drop" in content
    assert "release_issue" in content
    assert "headless" in content.lower()
    # Old field names must not survive the rename
    step6_start = content.find("6. Handle deferred")
    assert step6_start != -1, "sous-chef must have Step 6 deferred handling section"
    step6_text = content[step6_start:]
    assert "deferred_issues" not in step6_text, (
        "Steps 6a-6e must use deferred_groups, not deferred_issues"
    )
    assert "blocked_by" not in step6_text, "Steps 6a-6e must use gated_by, not blocked_by"


def test_sous_chef_has_headless_wait_rule() -> None:
    content = _sous_chef_text()
    assert "denied" in content and "Wait" in content
    assert "success: false" in content
