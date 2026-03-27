"""Tests for sous-chef/SKILL.md routing rule correctness."""

from __future__ import annotations


def test_sous_chef_stale_routing_rule_present():
    """sous-chef/SKILL.md must distinguish stale from context limit in routing rules."""
    from autoskillit.core.paths import pkg_root

    skill_md = (pkg_root() / "skills" / "sous-chef" / "SKILL.md").read_text()
    # Assert the compound routing discriminant, not just individual words — a heading or
    # description containing "stale" and "subtype" separately would satisfy a weaker check
    # without the routing rule being correctly wired.
    assert "subtype: stale" in skill_md or "subtype=stale" in skill_md, (
        "sous-chef/SKILL.md must contain 'subtype: stale' or 'subtype=stale' as a "
        "compound routing discriminant, not just the words 'stale' and 'subtype' separately"
    )
