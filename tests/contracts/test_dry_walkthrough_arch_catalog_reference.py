"""Contract test: dry-walkthrough Step 4 references the Arch Constraint Catalog."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_PATH = "skills_extended/dry-walkthrough/SKILL.md"


def _read_skill_md() -> str:
    from autoskillit.core import pkg_root

    return (pkg_root() / _SKILL_PATH).read_text()


def test_step4_references_arch_constraint_catalog():
    """Step 4 PROJECT RULES CHECKLIST must reference the Architectural Constraint Catalog."""
    content = _read_skill_md()
    assert "Architectural Constraint Catalog" in content, (
        "dry-walkthrough/SKILL.md Step 4 must reference the Architectural "
        "Constraint Catalog in resolve-review/SKILL.md to catch architectural "
        "constraint violations in plan code samples"
    )
