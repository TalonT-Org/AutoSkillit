"""Contract: Every worktree-modifying skill must carry the zero-changes prohibition.

Skills that create or modify files in a worktree can be tricked into finishing
with zero tracked source changes (only temp drafts, no committed code) while
still emitting completion markers. Each WORKTREE_SKILL must carry an explicit
prohibition that prevents models from justifying completion with temp-only
artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.core.types._type_constants import WORKTREE_SKILLS

pytestmark = [pytest.mark.small]

_CANARY_STRING = "never authorizes finishing with zero tracked source changes"


def _skill_md_path(skill_name: str) -> Path:
    return pkg_root() / "skills_extended" / skill_name / "SKILL.md"


@pytest.mark.parametrize("skill_name", sorted(WORKTREE_SKILLS))
def test_writer_skill_scope_fence_contains_zero_changes_prohibition(skill_name: str) -> None:
    """Each WORKTREE_SKILL SKILL.md must contain the zero-changes prohibition canary."""
    skill_md = _skill_md_path(skill_name)
    assert skill_md.exists(), f"{skill_md} does not exist"
    content = skill_md.read_text()
    assert _CANARY_STRING in content, (
        f"{skill_name}/SKILL.md must contain the phrase {_CANARY_STRING!r} "
        "to prevent models from finishing with zero tracked source changes. "
        "For skills with scope-fence sections, add the prohibition after the "
        "fence text. For skills without, add it to Critical Constraints NEVER list."
    )
