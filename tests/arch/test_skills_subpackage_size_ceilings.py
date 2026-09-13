"""Size-ceiling guard for the workspace/skills decomposition (#4833).

Every new shard plus both retained facades must stay under the 750-line
warning zone. The 1000-line hard ceiling is enforced globally by
``tests/arch/test_subpackage_isolation_size.py::test_no_src_module_exceeds_line_limit``,
so this guard only needs the
warning-zone check focused on the decomposed package.
"""

from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT
from tests.arch._line_budget import count_budget_lines

pytestmark = [pytest.mark.small]


_SKILLS_TARGETS: tuple[str, ...] = (
    "workspace/skills/__init__.py",
    "workspace/skills/_records.py",
    "workspace/skills/_overrides.py",
    "workspace/skills/_exploration.py",
    "workspace/skills/_visibility.py",
    "workspace/skills/_frontmatter.py",
    "workspace/skills/_format.py",
    "workspace/skills/_resources.py",
    "workspace/skill_capabilities/__init__.py",
    "workspace/skill_capabilities/_cache.py",
    "workspace/skill_capabilities/_scanner.py",
    "workspace/skill_capabilities/_authenticity.py",
    "workspace/skill_capabilities/_semantic_plan.py",
)


@pytest.mark.parametrize("rel_path", _SKILLS_TARGETS)
def test_skill_module_under_warning_zone(rel_path: str) -> None:
    """Every decomposed module must stay under the 750-line warning zone."""
    target = SRC_ROOT / rel_path
    line_count = count_budget_lines(target)
    assert line_count <= 750, (
        f"{rel_path}: {line_count} non-import lines (warning zone is 750). "
        f"Decompose further or justify."
    )
