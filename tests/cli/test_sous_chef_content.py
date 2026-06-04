"""Tests for sous-chef SKILL.md content invariants."""

from __future__ import annotations

import pytest

from autoskillit.cli._prompts import _read_full_sous_chef

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_sous_chef_prohibits_raw_recipe_reading():
    content = _read_full_sous_chef()
    assert "NEVER read recipe YAML files from the filesystem" in content


def test_sous_chef_content_no_frontmatter():
    """_read_full_sous_chef must strip YAML frontmatter metadata."""
    content = _read_full_sous_chef()
    assert content, "_read_full_sous_chef returned empty string"
    assert not content.startswith("---"), "Frontmatter delimiter still present"
    assert "uses_capabilities:" not in content, "Frontmatter field leaked into content"
