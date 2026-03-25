"""C-RPR-1: Contract tests for review-pr diff annotation inputs."""

from __future__ import annotations

from pathlib import Path

import yaml

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"
_SKILL_MD = (
    Path(__file__).parents[2] / "src/autoskillit/skills_extended/review-pr/SKILL.md"
)


def test_review_pr_contract_has_annotated_diff_path() -> None:
    """C-RPR-1a: review-pr contract must declare annotated_diff_path input."""
    raw = yaml.safe_load(_CONTRACTS_YAML.read_text())
    inputs = raw.get("skills", {}).get("review-pr", {}).get("inputs", [])
    names = [inp["name"] for inp in inputs]
    assert "annotated_diff_path" in names, (
        "review-pr contract must have an annotated_diff_path input entry"
    )


def test_review_pr_contract_has_hunk_ranges_path() -> None:
    """C-RPR-1b: review-pr contract must declare hunk_ranges_path input."""
    raw = yaml.safe_load(_CONTRACTS_YAML.read_text())
    inputs = raw.get("skills", {}).get("review-pr", {}).get("inputs", [])
    names = [inp["name"] for inp in inputs]
    assert "hunk_ranges_path" in names, (
        "review-pr contract must have a hunk_ranges_path input entry"
    )


def test_review_pr_skill_reads_annotated_diff_from_file() -> None:
    """review-pr SKILL.md must reference annotated_diff_path (no autoskillit import)."""
    skill_text = _SKILL_MD.read_text()
    assert "annotated_diff_path" in skill_text, (
        "review-pr SKILL.md must read annotated_diff_path from disk"
    )


def test_review_pr_skill_reads_hunk_ranges_from_file() -> None:
    """review-pr SKILL.md must reference hunk_ranges_path (no autoskillit import)."""
    skill_text = _SKILL_MD.read_text()
    assert "hunk_ranges_path" in skill_text, (
        "review-pr SKILL.md must read hunk_ranges_path from disk"
    )
