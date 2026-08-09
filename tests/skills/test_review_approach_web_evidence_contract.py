from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


SKILL_PATH = (
    Path(__file__).parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-approach"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_research_budget_is_total_and_non_recursive(skill_text: str) -> None:
    assert "at most five distinct research topics" in skill_text
    assert "at most five top-level children" in skill_text
    assert "at most eight web searches per child and forty total" in skill_text
    assert "must not launch Agent or Skill children" in skill_text
    assert "Spawn all topic children concurrently" in skill_text
    assert "join every child" in skill_text


def test_every_topic_has_one_terminal_ledger_entry(skill_text: str) -> None:
    assert "Create one ledger row for every selected topic before dispatch" in skill_text
    assert "exact child ID" in skill_text
    assert "update each row exactly once" in skill_text
    assert "answered | partial | blocked" in skill_text
    assert "must never be described as active" in skill_text


@pytest.mark.parametrize(
    ("outcome", "required_contract"),
    [
        ("all-success", "synthesize normally"),
        ("partial-success", "synthesize every usable cited result"),
        ("all-failed", "all rows are `blocked`"),
        ("nested-delegation-attempt", "nested delegation is a contract violation"),
        ("incomplete-evidence", "structured retryable failure"),
    ],
)
def test_completion_outcomes_are_explicit(
    skill_text: str, outcome: str, required_contract: str
) -> None:
    completion = skill_text.split("### Step 3: Synthesize", maxsplit=1)[1]
    assert outcome in completion
    assert required_contract in completion


def test_every_exit_preserves_report_and_output_token(skill_text: str) -> None:
    assert "Every completion branch, including retryable failure" in skill_text
    assert "writes the review report" in skill_text
    assert re.search(r"review_path\s*=\s*\{absolute_path_to_review_file\}", skill_text)
