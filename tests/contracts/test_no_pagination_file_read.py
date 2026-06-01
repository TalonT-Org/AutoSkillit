"""Contract guards for no-pagination file read instruction in high-turn skills."""

from pathlib import Path

import pytest

from tests._helpers import extract_always_block

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILLS_ROOT = (
    Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "skills_extended"
)

TARGET_SKILLS = [
    "implement-worktree-no-merge",
    "resolve-failures",
    "retry-worktree",
]


@pytest.fixture(params=TARGET_SKILLS, ids=TARGET_SKILLS)
def skill_text(request: pytest.FixtureRequest) -> str:
    path = SKILLS_ROOT / request.param / "SKILL.md"
    assert path.exists(), f"SKILL.md not found at {path}"
    return path.read_text()


def test_no_pagination_instruction_present(skill_text: str) -> None:
    """The ALWAYS block must contain the no-pagination file read instruction."""
    always_block = extract_always_block(skill_text)
    assert "single call without a `limit` parameter" in always_block, (
        "ALWAYS block must instruct reading files in a single call without limit"
    )


def test_no_pagination_instruction_prohibits_sequential_offset(skill_text: str) -> None:
    """The instruction must explicitly prohibit sequential offset reads."""
    always_block = extract_always_block(skill_text)
    assert "Do not paginate" in always_block, (
        "ALWAYS block must explicitly prohibit paginated sequential offset reads"
    )


def test_no_pagination_instruction_permits_targeted_reads(skill_text: str) -> None:
    """The instruction must permit targeted limit/offset for known files."""
    always_block = extract_always_block(skill_text)
    assert "targeted section reads" in always_block, (
        "ALWAYS block must permit limit/offset for targeted reads of already-read files"
    )
