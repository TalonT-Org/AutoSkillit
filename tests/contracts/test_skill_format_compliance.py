"""Universal contract test: all bundled SKILL.md files have valid frontmatter.

Parametrized over _all_skill_mds() from tests/contracts/conftest.py.
Each SKILL.md must parse valid frontmatter with name matching parent dir
and non-empty description.
"""

from __future__ import annotations

import pytest

from tests.contracts.conftest import _all_skill_mds

from autoskillit.workspace.skill_format import (
    parse_frontmatter_content,
    validate_skill_frontmatter,
)


class TestSkillFormatCompliance:
    @pytest.mark.parametrize("skill_name,content", _all_skill_mds())
    def test_skill_md_has_valid_frontmatter(
        self, skill_name: str, content: str
    ) -> None:
        fm = parse_frontmatter_content(content)
        errors = validate_skill_frontmatter(fm, skill_name)
        assert errors == [], (
            f"SKILL.md for {skill_name!r} has frontmatter errors: {errors}"
        )
