"""Contract guards for no-pagination file read instruction in high-turn skills."""

from pathlib import Path

import pytest

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


def _extract_always_block(text: str) -> str:
    """Extract the ALWAYS block from Critical Constraints, or "" if absent."""
    always_idx = text.find("**ALWAYS:**")
    if always_idx == -1:
        return ""
    next_section = text.find("\n## ", always_idx)
    return text[always_idx:next_section] if next_section != -1 else text[always_idx:]


def test_no_pagination_instruction_present(skill_text: str) -> None:
    """The ALWAYS block must contain the no-pagination file read instruction."""
    always_block = _extract_always_block(skill_text)
    assert "single call without a `limit` parameter" in always_block, (
        "ALWAYS block must instruct reading files in a single call without limit"
    )


def test_no_pagination_instruction_prohibits_sequential_offset(skill_text: str) -> None:
    """The instruction must explicitly prohibit sequential offset reads."""
    always_block = _extract_always_block(skill_text)
    assert "Do not paginate" in always_block, (
        "ALWAYS block must explicitly prohibit paginated sequential offset reads"
    )


def test_no_pagination_instruction_permits_targeted_reads(skill_text: str) -> None:
    """The instruction must permit targeted limit/offset for known files."""
    always_block = _extract_always_block(skill_text)
    assert "targeted section reads" in always_block, (
        "ALWAYS block must permit limit/offset for targeted reads of already-read files"
    )
