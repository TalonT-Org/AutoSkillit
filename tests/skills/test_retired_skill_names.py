"""Tests enforcing the RETIRED_SKILL_NAMES convention.

Analogous to test_no_retired_name_has_a_live_file in tests/hooks/.
Each entry in RETIRED_SKILL_NAMES must NOT have a live skill directory
under skills/ or skills_extended/.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_no_retired_skill_name_has_a_live_directory() -> None:
    """No retired skill name may have a live directory under skills/ or skills_extended/."""
    from autoskillit.core import RETIRED_SKILL_NAMES
    from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir

    for name in RETIRED_SKILL_NAMES:
        for skill_dir in (bundled_skills_dir(), bundled_skills_extended_dir()):
            candidate = skill_dir / name
            assert not candidate.is_dir(), (
                f"Retired skill name '{name}' has a live directory at {candidate}. "
                "Remove the directory or remove it from RETIRED_SKILL_NAMES."
            )


def test_retired_skill_names_are_lowercase() -> None:
    """All RETIRED_SKILL_NAMES entries must be lowercase (kebab-case)."""
    from autoskillit.core import RETIRED_SKILL_NAMES

    bad = sorted(n for n in RETIRED_SKILL_NAMES if n != n.lower())
    assert not bad, f"RETIRED_SKILL_NAMES entries must be lowercase. Offending: {bad}"


def test_scan_directory_raises_on_retired_skill(tmp_path: Path) -> None:
    """DefaultSkillResolver raises RuntimeError if a retired skill directory is discovered."""
    from autoskillit.core import RETIRED_SKILL_NAMES
    from autoskillit.workspace.skills import SkillSource, _scan_directory

    retired_name = next(iter(sorted(RETIRED_SKILL_NAMES)))
    skill_dir = tmp_path / retired_name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ncategories: []\n---\n# Fake\n")

    with pytest.raises(RuntimeError, match=retired_name):
        list(_scan_directory(SkillSource.BUNDLED_EXTENDED, tmp_path))
