"""Contract test: dry-walkthrough declares its Arch Constraint Catalog resource."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.workspace.skill_format import read_skill_frontmatter

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_SKILL_PATH = "skills_extended/dry-walkthrough/SKILL.md"


def _skill_path() -> Path:
    from autoskillit.core import pkg_root

    return pkg_root() / _SKILL_PATH


def test_dry_walkthrough_declares_arch_constraint_catalog_resource():
    """The catalog is provided data, not a source-tree prose dependency."""
    frontmatter = read_skill_frontmatter(_skill_path())
    assert frontmatter.data is not None, frontmatter.error
    assert frontmatter.data.get("requires_resources") == ["arch-constraint-catalog"], (
        "dry-walkthrough must declare requires_resources: [arch-constraint-catalog] "
        "so the architectural constraint catalog is delivered with the projected skill"
    )
