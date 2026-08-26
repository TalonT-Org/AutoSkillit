"""Size-ceiling guard for the workspace/skills decomposition (#4833).

Every new shard plus both retained facades must stay under the warning zone
(750 lines) and the hard ceiling (1000 lines). The existing
``test_no_src_module_exceeds_line_limit`` already enforces the 1000-line
hard ceiling on every source module; this guard adds the 750-line warning-zone
assertion focused on the decomposed package.
"""

from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.small]


_SKILLS_TARGETS: tuple[str, ...] = (
    "workspace/skills.py",
    "workspace/skill_capabilities.py",
    "workspace/skills_records.py",
    "workspace/skills_overrides.py",
    "workspace/skills_exploration.py",
    "workspace/skills_visibility.py",
    "workspace/skills_frontmatter.py",
    "workspace/skill_capability_cache.py",
    "workspace/skill_capability_scanner.py",
    "workspace/skill_capability_authenticity.py",
    "workspace/skill_semantic_plan.py",
)


@pytest.mark.parametrize("rel_path", _SKILLS_TARGETS)
def test_skill_module_under_warning_zone(rel_path: str) -> None:
    """Every decomposed module must stay under the 750-line warning zone."""
    target = SRC_ROOT / rel_path
    line_count = len(target.read_text().splitlines())
    assert line_count <= 750, (
        f"{rel_path}: {line_count} lines (warning zone is 750). Decompose further or justify."
    )


@pytest.mark.parametrize("rel_path", _SKILLS_TARGETS)
def test_skill_module_under_hard_ceiling(rel_path: str) -> None:
    """Every decomposed module must stay under the 1000-line hard ceiling."""
    target = SRC_ROOT / rel_path
    line_count = len(target.read_text().splitlines())
    assert line_count <= 1000, f"{rel_path}: {line_count} lines (hard ceiling is 1000)"
