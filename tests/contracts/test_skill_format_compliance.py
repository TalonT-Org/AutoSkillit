"""Universal contract test: all bundled SKILL.md files have valid frontmatter.

Parametrized over _all_skill_mds() from tests/contracts/conftest.py.
Each SKILL.md must parse valid frontmatter with name matching parent dir
and non-empty description.
"""

from __future__ import annotations

import pytest

from autoskillit.workspace.skill_format import (
    parse_frontmatter_content,
    validate_skill_frontmatter,
)
from tests.contracts.conftest import _all_skill_mds

try:
    _SKILL_MDS = _all_skill_mds()
except Exception:
    _SKILL_MDS = []


class TestSkillFormatCompliance:
    def test_skill_mds_collection_nonempty(self) -> None:
        assert _SKILL_MDS, "No bundled SKILL.md files found — check package installation"

    @pytest.mark.parametrize("skill_name,content", _SKILL_MDS, ids=[n for n, _ in _SKILL_MDS])
    def test_skill_md_has_valid_frontmatter(self, skill_name: str, content: str) -> None:
        fm = parse_frontmatter_content(content)
        errors = validate_skill_frontmatter(fm, skill_name)
        assert errors == [], f"SKILL.md for {skill_name!r} has frontmatter errors: {errors}"
