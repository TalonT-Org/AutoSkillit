"""
Contract: any SKILL.md that generates mermaid diagrams must either embed the canonical
9-class palette or explicitly mandate loading the mermaid skill before drawing.
Parametrized over all qualifying skills — self-updating as skills are added.
"""

import re
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

# Signals that a skill generates mermaid diagrams
_MERMAID_GENERATOR_PATTERN = re.compile(
    r"```mermaid|mermaid block|diagram_path|classDef|"
    r"mermaid diagram|generate.{0,30}diagram|create.{0,30}diagram",
    re.IGNORECASE,
)

# Canonical class names — at least 7 of 9 must appear
_CANONICAL_CLASSES = frozenset(
    {
        "cli",
        "stateNode",
        "handler",
        "phase",
        "output",
        "integration",
        "newComponent",
        "detector",
        "gap",
    }
)

# Alternative: explicit mandate to load mermaid skill before drawing
_MERMAID_LOAD_PATTERN = re.compile(
    r"LOAD.{0,50}/autoskillit:mermaid|"
    r"load.{0,50}mermaid.{0,30}Skill tool|"
    r"Using ONLY classDef styles from the mermaid skill",
    re.IGNORECASE,
)


def _find_diagram_generating_skills() -> list[tuple[str, Path]]:
    results = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text()
        if _MERMAID_GENERATOR_PATTERN.search(text):
            results.append((skill_dir.name, skill_md))
    return results


_DIAGRAM_SKILLS = _find_diagram_generating_skills()


@pytest.mark.parametrize(
    "skill_name,skill_md",
    _DIAGRAM_SKILLS,
    ids=[s[0] for s in _DIAGRAM_SKILLS],
)
def test_diagram_generating_skill_has_palette_or_mermaid_load(skill_name: str, skill_md: Path):
    """
    Any skill that generates mermaid diagrams must either embed at least 7 of the 9
    canonical class names OR mandate loading the mermaid skill before drawing.
    Prevents invented class names and unstyled gray diagrams.
    """
    text = skill_md.read_text()
    found = {name for name in _CANONICAL_CLASSES if name in text}
    has_palette = len(found) >= 7
    has_mermaid_load = bool(_MERMAID_LOAD_PATTERN.search(text))
    assert has_palette or has_mermaid_load, (
        f"{skill_name}/SKILL.md generates mermaid diagrams but neither embeds the "
        f"canonical palette (found only {sorted(found)} of {len(_CANONICAL_CLASSES)}) "
        f"nor mandates loading the mermaid skill before drawing. "
        f"Add the 9-classDef block from mermaid/SKILL.md, or add to the checklist: "
        f"'Using ONLY classDef styles from the mermaid skill (no invented colors)'."
    )
