"""Assert that no SKILL.md body references %%ORDER_UP%%."""
from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root

_SKILLS_DIRS = [pkg_root() / "skills", pkg_root() / "skills_extended"]


def _all_skill_mds() -> list[Path]:
    paths = []
    for d in _SKILLS_DIRS:
        paths.extend(d.glob("*/SKILL.md"))
    return sorted(paths)


@pytest.mark.parametrize("skill_md", _all_skill_mds(), ids=lambda p: p.parent.name)
def test_no_order_up_in_skill_body(skill_md: Path) -> None:
    """No SKILL.md should reference %%ORDER_UP%% — completion is injected at prompt level."""
    text = skill_md.read_text()
    assert "%%ORDER_UP%%" not in text, (
        f"{skill_md.parent.name}/SKILL.md contains %%ORDER_UP%% reference.\n"
        "The completion directive is injected by _inject_completion_directive() "
        "in commands.py. Remove it from the skill body."
    )
