"""Tests for the unknown-required-pack semantic rule."""

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(requires_packs: list[str]) -> Recipe:
    return Recipe(
        name="test",
        description="test recipe",
        version="0.7.2",
        requires_packs=requires_packs,
        steps={"stop": RecipeStep(action="stop")},
    )


def test_unknown_pack_produces_warning():
    """Pack name not in PACK_REGISTRY produces a WARNING finding."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe(["nonexistent-pack"])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert findings
    assert findings[0].severity == Severity.WARNING
    assert "nonexistent-pack" in findings[0].message


def test_known_pack_produces_no_finding():
    """Known pack name (in PACK_REGISTRY) produces no finding."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe(["research"])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert not findings


def test_mixed_packs_flags_only_unknown():
    """Only unknown packs are flagged; known packs pass silently."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe(["research", "bogus-pack"])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert len(findings) == 1
    assert "bogus-pack" in findings[0].message


def test_empty_requires_packs_produces_no_finding():
    """Recipes without requires_packs produce no finding."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration

    recipe = _make_recipe([])
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert not findings


def test_all_builtin_packs_pass():
    """Every pack in PACK_REGISTRY is a valid name (no self-flagging)."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration
    from autoskillit.core import PACK_REGISTRY

    recipe = _make_recipe(list(PACK_REGISTRY.keys()))
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "unknown-required-pack"]
    assert not findings, f"Built-in packs must not trigger unknown-required-pack: {findings}"
