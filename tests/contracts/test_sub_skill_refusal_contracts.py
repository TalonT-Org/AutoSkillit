"""
Cross-skill contract: every SKILL.md that invokes sub-skills via the Skill tool must contain
explicit refusal handling language. Parametrized over all qualifying skills — self-updating
as new skills are added to skills_extended/.
"""

import re
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

# Identifies a SKILL.md as invoking sub-skills via the Skill tool
_SUB_SKILL_CALL_PATTERN = re.compile(
    r"Skill tool.{0,200}/autoskillit:|"
    r"/autoskillit:.{0,200}Skill tool|"
    r"LOAD.{0,100}/autoskillit:|"
    r"Load.{0,100}/autoskillit:",
)

# Refusal detection language
_REFUSAL_SIGNAL_PATTERN = re.compile(
    r"disable-model-invocation|cannot be used|Skill tool returns.{0,50}error|"
    r"refused|Skill tool fails|skill.{0,50}unavailable",
    re.IGNORECASE,
)

# Required action on refusal — specific phrases unlikely to appear in non-refusal contexts
_ACTION_SIGNAL_PATTERN = re.compile(
    r"proceed without|discard|log.{0,30}warning|fail.{0,30}clean|abort.{0,20}step",
    re.IGNORECASE,
)


def _find_sub_skill_calling_skills() -> list[tuple[str, Path]]:
    results = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text(encoding="utf-8")
        if _SUB_SKILL_CALL_PATTERN.search(text):
            results.append((skill_dir.name, skill_md))
    return results


_SUB_SKILL_CALLERS = _find_sub_skill_calling_skills()


@pytest.mark.parametrize(
    "skill_name,skill_md",
    _SUB_SKILL_CALLERS,
    ids=[s[0] for s in _SUB_SKILL_CALLERS],
)
def test_sub_skill_calling_skill_has_refusal_handler(skill_name: str, skill_md: Path):
    """
    Structural ratchet: any skill that invokes sub-skills via the Skill tool must
    document what to do when the Skill tool refuses the invocation. New qualifying
    skills without this handler fail CI immediately without requiring code review.
    """
    text = skill_md.read_text(encoding="utf-8")
    has_refusal = bool(_REFUSAL_SIGNAL_PATTERN.search(text))
    has_action = bool(_ACTION_SIGNAL_PATTERN.search(text))
    assert has_refusal, (
        f"{skill_name}/SKILL.md invokes sub-skills via the Skill tool but contains no "
        f"documentation of what to do when the Skill tool refuses the invocation. "
        f"Add a refusal handler near the Skill tool call: describe what happens when the "
        f"tool returns 'cannot be used' or 'disable-model-invocation', and what action follows."
    )
    assert has_action, (
        f"{skill_name}/SKILL.md has refusal detection language but no prescribed action. "
        f"Specify: proceed without, discard, fail clean, log warning, or abort step."
    )


def test_mermaid_skill_itself_not_a_sub_skill_caller():
    """
    Sanity: the mermaid skill is a leaf and should not appear in the parametrized list.
    Confirms the detector does not over-fire on leaf skills.
    """
    caller_names = {s[0] for s in _SUB_SKILL_CALLERS}
    assert "mermaid" not in caller_names, (
        "mermaid/SKILL.md was detected as a sub-skill caller — review the detection pattern."
    )
