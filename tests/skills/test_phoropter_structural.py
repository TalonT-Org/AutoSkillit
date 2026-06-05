from __future__ import annotations

import pytest

from autoskillit.core import load_yaml, pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.workspace.skill_format import parse_frontmatter_content

SKILLS_DIR = pkg_root() / "skills_extended"

_REGISTRY = load_yaml(pkg_root() / "assets" / "phoropter-registry.yaml")

_LENS_PAIRS: list[tuple[str, str]] = sorted(
    (family, child.name[len(family) + 1 :])
    for family in _REGISTRY["families"]
    for child in sorted(SKILLS_DIR.iterdir())
    if child.is_dir() and child.name.startswith(f"{family}-")
)

_IMPLEMENTED_FAMILIES = frozenset({"vis-lens", "arch-lens", "exp-lens"})

_EXPECTED_LENS_COUNT = sum(meta["lens_count"] for meta in _REGISTRY["families"].values())

# TODO: load from src/autoskillit/assets/phoropter-registry.yaml (T2-P1-A5)
# once that file provides a machine-readable dial_skill → output_tokens mapping.
_DIAL_SKILL_MAP: dict[str, str | None] = {
    "vis-lens": "select-vis-lenses",
    "review-design": "classify-experiment-type",
    # arch-lens and exp-lens dial skills (prepare-pr, prepare-research-pr) DO emit
    # selected_lenses and lens_context_paths tokens, but they are PR-preparation
    # skills — not lens-selection dial skills.  This map tracks only families whose
    # dial skill's primary purpose is lens/dimension selection.
    "arch-lens": None,
    "exp-lens": None,
}

_DIAL_SKILL_PAIRS: list[tuple[str, str]] = [
    (family, skill) for family, skill in _DIAL_SKILL_MAP.items() if skill is not None
]

RESEARCH_RECIPE = load_recipe(builtin_recipes_dir() / "research.yaml")
RESEARCH_DESIGN_RECIPE = load_recipe(builtin_recipes_dir() / "research-design.yaml")

_RECIPE_FAMILIES: frozenset[str] = frozenset(
    step.phoropter_family
    for recipe in (RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE)
    for step in recipe.steps.values()
    if step.phoropter_family and step.phoropter_family in _REGISTRY["families"]
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def test_discovery_is_non_empty_and_covers_all_families() -> None:
    assert len(_LENS_PAIRS) >= _EXPECTED_LENS_COUNT
    discovered_families = {family for family, _ in _LENS_PAIRS}
    for expected in _IMPLEMENTED_FAMILIES:
        assert expected in discovered_families, f"{expected} not found in discovered lens pairs"


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_phoropter_lens_structure(family: str, slug: str) -> None:
    family_meta = _REGISTRY["families"][family]

    path = SKILLS_DIR / f"{family}-{slug}" / "SKILL.md"
    assert path.exists(), f"{family}-{slug}/SKILL.md is missing"

    text = path.read_text()

    assert "## Arguments" in text, f"{family}-{slug} missing ## Arguments section"
    assert "context_path" in text, f"{family}-{slug} must document context_path"
    assert "Step 0" in text, f"{family}-{slug} must have Step 0"
    assert "diagram_path" in text, f"{family}-{slug} must mention diagram_path"

    fm = parse_frontmatter_content(text)
    assert fm.get("categories") == [family], (
        f"{family}-{slug} frontmatter categories must be [{family}], got {fm.get('categories')}"
    )
    assert fm.get("activate_deps") == family_meta["activate_deps"], (
        f"{family}-{slug} frontmatter activate_deps must be {family_meta['activate_deps']}, "
        f"got {fm.get('activate_deps')}"
    )

    if family_meta["arg_interface"] == "2-arg":
        assert "experiment_plan_path" in text, (
            f"{family}-{slug} (2-arg) must document experiment_plan_path"
        )

    if family == "vis-lens":
        if slug in family_meta.get("composite_slugs", []):
            assert "yaml:spec-index" in text, (
                f"{family}-{slug} (composite) must contain yaml:spec-index"
            )
        else:
            assert "yaml:figure-spec" in text, f"{family}-{slug} must contain yaml:figure-spec"

    if family_meta.get("output_prefix"):
        assert family_meta["output_prefix"] in text, (
            f"{family}-{slug} output path must use {family_meta['output_prefix']} prefix"
        )

    entry = family_meta.get("lens_metadata", {}).get(slug, {})
    special = entry.get("special_assertions", [])
    if "tradition_slug" in special and "two_stage_matching" in special:
        assert "tradition_slug" in text, f"{family}-{slug} must document tradition_slug"
        assert "Stage A" in text or "stage A" in text, f"{family}-{slug} must document Stage A"
        assert "Stage B" in text or "stage B" in text, f"{family}-{slug} must document Stage B"
        assert "venue_specific_appendices" in text or "venue appendix" in text, (
            f"{family}-{slug} must document venue_specific_appendices"
        )


@pytest.mark.parametrize("family,dial_skill", _DIAL_SKILL_PAIRS)
def test_dial_skill_skill_md_exists(family: str, dial_skill: str) -> None:
    assert (SKILLS_DIR / dial_skill / "SKILL.md").exists(), (
        f"{family} dial skill {dial_skill}/SKILL.md is missing"
    )


@pytest.mark.parametrize("family,dial_skill", _DIAL_SKILL_PAIRS)
def test_dial_skill_emits_output_tokens(family: str, dial_skill: str) -> None:
    text = (SKILLS_DIR / dial_skill / "SKILL.md").read_text()
    assert "selected_lenses" in text, f"{family} dial skill {dial_skill} must emit selected_lenses"
    assert "lens_context_paths" in text, (
        f"{family} dial skill {dial_skill} must emit lens_context_paths"
    )


def test_prefixed_step_naming_convention() -> None:
    recipes = [RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE]
    for family_name, meta in _REGISTRY["families"].items():
        if family_name not in _RECIPE_FAMILIES:
            continue
        prefix = meta.get("step_naming", {}).get("prefix")
        if not prefix:
            continue
        expected = [f"{prefix}_dial", f"{prefix}_apply", f"{prefix}_synthesize"]
        assert any(all(name in recipe.steps for name in expected) for recipe in recipes), (
            f"Family {family_name} (prefix={prefix!r}) requires steps "
            f"{expected} in at least one recipe"
        )


def test_canonical_step_naming_convention() -> None:
    recipes = [RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE]
    canonical_names = ["dial", "apply", "synthesize"]
    for family_name, meta in _REGISTRY["families"].items():
        if family_name not in _RECIPE_FAMILIES:
            continue
        prefix = meta.get("step_naming", {}).get("prefix")
        if prefix is not None:
            continue
        assert any(
            all(
                name in recipe.steps and recipe.steps[name].phoropter_family == family_name
                for name in canonical_names
            )
            for recipe in recipes
        ), (
            f"Family {family_name} (canonical naming) requires steps "
            f"{canonical_names} with phoropter_family={family_name!r} "
            f"in at least one recipe"
        )


def test_vis_lens_phoropter_family_annotation() -> None:
    """Verify vis-lens steps carry phoropter_family == 'vis-lens' (post-P3 state)."""
    vis_meta = _REGISTRY["families"]["vis-lens"]
    prefix = vis_meta["step_naming"]["prefix"]
    step_names = [f"{prefix}_dial", f"{prefix}_apply", f"{prefix}_synthesize"]
    for recipe in (RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE):
        for step_name in step_names:
            assert step_name in recipe.steps, f"{step_name} missing from {recipe.name}"
            assert recipe.steps[step_name].phoropter_family == "vis-lens", (
                f"{step_name} in {recipe.name} must have "
                f"phoropter_family='vis-lens', got "
                f"{recipe.steps[step_name].phoropter_family!r}"
            )
