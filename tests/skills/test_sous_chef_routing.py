"""Tests for sous-chef/SKILL.md routing rule correctness."""

from __future__ import annotations


def test_sous_chef_stale_routing_rule_present():
    """sous-chef/SKILL.md must distinguish stale from context limit in routing rules."""
    from autoskillit.core.paths import pkg_root

    skill_md = (pkg_root() / "skills" / "sous-chef" / "SKILL.md").read_text()
    assert "stale" in skill_md.lower()
    # subtype must appear as a routing discriminant
    assert "subtype" in skill_md
