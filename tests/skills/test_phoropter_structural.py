from __future__ import annotations

import re

import pytest

from autoskillit.core import load_yaml, pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.workspace.skill_format import parse_frontmatter_content
from tests._helpers import IMPLEMENTED_FAMILIES

SKILLS_DIR = pkg_root() / "skills_extended"
_REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"

# Lens families are filesystem-discovered. The registry carries only
# ``step_naming.prefix`` and is read exclusively by
# ``rules_phoropter_adjacency.py``; every other knob is derived from each
# lens's SKILL.md content.

_LENS_PAIRS: list[tuple[str, str]] = sorted(
    (family, child.name[len(family) + 1 :])
    for family in IMPLEMENTED_FAMILIES
    for child in sorted(SKILLS_DIR.iterdir())
    if child.is_dir() and child.name.startswith(f"{family}-")
)


def _load_registry_prefixes() -> dict[str, str | None]:
    """Read ``step_naming.prefix`` per family from the registry.

    Mirrors ``_load_family_prefixes()`` in
    ``recipe/rules/rules_phoropter_adjacency.py`` so tests assert against
    the same source of truth production reads.
    """
    registry = load_yaml(_REGISTRY_PATH)
    families = registry.get("families", {})
    return {
        family: entry.get("step_naming", {}).get("prefix") for family, entry in families.items()
    }


# Maps below name the families each retired registry field described.
# They are consumed by body-derived tests as the expected interface —
# keep them in sync with the SKILL.md sources.
FAMILY_ARG_INTERFACE: dict[str, str] = {
    "arch-lens": "1-arg",
    "exp-lens": "2-arg",
    "vis-lens": "2-arg",
}

_COMPOSITE_SLUGS: dict[tuple[str, str], list[str]] = {
    ("vis-lens", "always-on"): ["always-on"],
}

# ``arch-lens`` and ``exp-lens`` dial skills emit the same output tokens but
# are PR-preparation skills, not lens-selection dial skills.
_DIAL_SKILLS: dict[str, str | None] = {
    "vis-lens": "select-vis-lenses",
    "arch-lens": None,
    "exp-lens": None,
}

_DIAL_SKILL_PAIRS: list[tuple[str, str]] = sorted(
    (family, skill) for family, skill in _DIAL_SKILLS.items() if skill is not None
)

RESEARCH_RECIPE = load_recipe(builtin_recipes_dir() / "research.yaml")
RESEARCH_DESIGN_RECIPE = load_recipe(builtin_recipes_dir() / "research-design.yaml")

_RECIPE_FAMILIES: frozenset[str] = frozenset(
    step.phoropter_family
    for recipe in (RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE)
    for step in recipe.steps.values()
    if step.phoropter_family and step.phoropter_family in IMPLEMENTED_FAMILIES
)

_FAMILY_PREFIX: dict[str, str | None] = {
    family: prefix
    for family, prefix in _load_registry_prefixes().items()
    if family in _RECIPE_FAMILIES
}

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def _extract_arguments_section(skill_md: str) -> str:
    """Extract the content of the SKILL.md ``## Arguments`` section.

    The section is delimited by ``## Arguments`` and the next ``## ``
    heading. Returns the empty string if the section is missing.
    """
    match = re.search(r"## Arguments\s*\n(.*?)(?=\n## |\Z)", skill_md, re.DOTALL)
    return match.group(1) if match else ""


def test_discovery_is_non_empty_and_covers_all_families() -> None:
    """Each implemented family must have at least one discovered lens directory."""
    discovered_families = {family for family, _ in _LENS_PAIRS}
    for expected in IMPLEMENTED_FAMILIES:
        count = sum(1 for family, _ in _LENS_PAIRS if family == expected)
        assert count > 0, f"Discovery found zero {expected}-* lenses under {SKILLS_DIR}"
        assert expected in discovered_families, f"{expected} not found in discovered lens pairs"


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_phoropter_lens_structure(family: str, slug: str) -> None:
    """SKILL.md content contract for every discovered lens.

    All checks derive from the SKILL.md file (body + frontmatter). The
    registry only contributes ``step_naming.prefix`` (see ``_FAMILY_PREFIX``).
    """
    path = SKILLS_DIR / f"{family}-{slug}" / "SKILL.md"
    assert path.exists(), f"{family}-{slug}/SKILL.md is missing"

    text = path.read_text()

    assert "## Arguments" in text, f"{family}-{slug} missing ## Arguments section"
    assert "context_path" in text, f"{family}-{slug} must document context_path"
    assert "Step 0" in text, f"{family}-{slug} must have Step 0"
    assert "diagram_path" in text, f"{family}-{slug} must mention diagram_path"

    parsed = parse_frontmatter_content(text)
    assert parsed.is_valid and parsed.data is not None, (
        f"{family}-{slug} frontmatter must parse, got {parsed.error}"
    )
    fm = parsed.data
    assert fm.get("categories") == [family], (
        f"{family}-{slug} frontmatter categories must be [{family}], got {fm.get('categories')}"
    )
    assert fm.get("activate_deps") == ["mermaid"], (
        f"{family}-{slug} frontmatter activate_deps must be ['mermaid'], "
        f"got {fm.get('activate_deps')}"
    )

    if FAMILY_ARG_INTERFACE[family] == "2-arg":
        assert "experiment_plan_path" in text, (
            f"{family}-{slug} (2-arg) must document experiment_plan_path"
        )

    if family == "vis-lens":
        if (family, slug) in _COMPOSITE_SLUGS:
            assert "yaml:spec-index" in text, (
                f"{family}-{slug} (composite) must contain yaml:spec-index"
            )
        else:
            assert "yaml:figure-spec" in text, f"{family}-{slug} must contain yaml:figure-spec"

    if (family, slug) == ("vis-lens", "methodology-norms"):
        assert "tradition_slug" in text, f"{family}-{slug} must document tradition_slug"
        assert "Stage A" in text or "stage A" in text, f"{family}-{slug} must document Stage A"
        assert "Stage B" in text or "stage B" in text, f"{family}-{slug} must document Stage B"
        assert "venue_specific_appendices" in text or "venue appendix" in text, (
            f"{family}-{slug} must document venue_specific_appendices"
        )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_arg_interface_derived_from_skill_md(family: str, slug: str) -> None:
    """Body-derived arg-interface must match the family-level expected value."""
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    args_section = _extract_arguments_section(skill_md)
    has_experiment_plan = "experiment_plan_path" in args_section
    derived = "2-arg" if has_experiment_plan else "1-arg"
    assert derived == FAMILY_ARG_INTERFACE[family], (
        f"{family}-{slug}: SKILL.md ## Arguments shape implies {derived}, "
        f"but FAMILY_ARG_INTERFACE[{family!r}] = {FAMILY_ARG_INTERFACE[family]!r}"
    )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_activate_deps_from_frontmatter(family: str, slug: str) -> None:
    """Every lens declares ``activate_deps: ['mermaid']`` in its frontmatter."""
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    frontmatter = parse_frontmatter_content(skill_md).data
    deps = (frontmatter or {}).get("activate_deps", [])
    assert deps == ["mermaid"], f"{family}-{slug} activate_deps = {deps}, expected ['mermaid']"


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_composite_slugs_from_body(family: str, slug: str) -> None:
    """Body-derived composite flag must match the family-level expectation."""
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    derived_is_composite = "yaml:spec-index" in skill_md
    declared = (family, slug) in _COMPOSITE_SLUGS
    assert derived_is_composite == declared, (
        f"{family}-{slug}: body has yaml:spec-index={derived_is_composite}, "
        f"_COMPOSITE_SLUGS declares composite={declared}"
    )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_output_prefix_from_body(family: str, slug: str) -> None:
    """Body-derived vis-lens prefix marker must agree with the family."""
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    if family == "vis-lens":
        assert "vis_spec_" in skill_md, (
            f"{family}-{slug} must reference the vis_spec_ output prefix"
        )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_lens_metadata_special_assertions_from_body(family: str, slug: str) -> None:
    """vis-lens-methodology-norms must declare its two special assertions."""
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    assertions: list[str] = []
    if "tradition_slug" in skill_md:
        assertions.append("tradition_slug")
    if "Stage A" in skill_md or "two_stage_matching" in skill_md:
        assertions.append("two_stage_matching")
    expected = (
        ["tradition_slug", "two_stage_matching"]
        if (family == "vis-lens" and slug == "methodology-norms")
        else []
    )
    assert assertions == expected, (
        f"{family}-{slug}: derived assertions {assertions} != expected {expected}"
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
    for family_name, prefix in _FAMILY_PREFIX.items():
        if family_name not in _RECIPE_FAMILIES:
            continue
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
    for family_name, prefix in _FAMILY_PREFIX.items():
        if family_name not in _RECIPE_FAMILIES:
            continue
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
    prefix = _FAMILY_PREFIX["vis-lens"]
    assert prefix is not None, "vis-lens must have a non-null step_naming.prefix"
    step_names = [f"{prefix}_dial", f"{prefix}_apply", f"{prefix}_synthesize"]
    for recipe in (RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE):
        for step_name in step_names:
            assert step_name in recipe.steps, f"{step_name} missing from {recipe.name}"
            assert recipe.steps[step_name].phoropter_family == "vis-lens", (
                f"{step_name} in {recipe.name} must have "
                f"phoropter_family='vis-lens', got "
                f"{recipe.steps[step_name].phoropter_family!r}"
            )
