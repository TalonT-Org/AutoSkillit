"""Contract tests for sous-chef deferred issue escalation (T6/T7)."""

from __future__ import annotations

from pathlib import Path


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
    assert "deferred_issues" in content
    assert "AskUserQuestion" in content
    assert "Wait" in content and "Proceed" in content and "Drop" in content
    assert "release_issue" in content
    assert "headless" in content.lower()


def test_sous_chef_has_headless_wait_rule() -> None:
    content = _sous_chef_text()
    assert "denied" in content and "Wait" in content
    assert "success: false" in content
