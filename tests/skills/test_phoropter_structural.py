from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.workspace.skill_format import parse_frontmatter_content

SKILLS_DIR = pkg_root() / "skills_extended"

# Phoropter lens families are now filesystem-discovered; the registry at
# ``src/autoskillit/assets/phoropter-registry.yaml`` carries only
# ``step_naming.prefix`` and is read exclusively by
# ``src/autoskillit/recipe/rules/rules_phoropter_adjacency.py``. Every other
# phoropter knob is derived from each lens's SKILL.md frontmatter / body.

# Hardcoded family set — matches the implemented families on ``develop``.
# ``refactor-lens`` is a designed-only family with zero lens directories and
# is excluded from discovery.
_IMPLEMENTED_FAMILIES: frozenset[str] = frozenset({"arch-lens", "vis-lens", "exp-lens"})

_LENS_PAIRS: list[tuple[str, str]] = sorted(
    (family, child.name[len(family) + 1 :])
    for family in _IMPLEMENTED_FAMILIES
    for child in sorted(SKILLS_DIR.iterdir())
    if child.is_dir() and child.name.startswith(f"{family}-")
)

# Module-level — replaces the registry's ``arg_interface`` leaf. The two-arg
# shape is detected from SKILL.md ``## Arguments`` structure (see T3).
FAMILY_ARG_INTERFACE: dict[str, str] = {
    "arch-lens": "1-arg",  # all 13 lenses take only context_path
    "exp-lens": "2-arg",  # all 18 lenses take context_path + experiment_plan_path
    "vis-lens": "2-arg",  # all 12 lenses take context_path + experiment_plan_path
}

# Module-level — replaces the registry's ``composite_slugs`` leaf. Only
# ``vis-lens-always-on`` is currently a composite lens (it embeds multiple
# spec indexes via the ``yaml:spec-index`` body marker); all other lenses
# are atomic.
_COMPOSITE_SLUGS: dict[tuple[str, str], list[str]] = {
    ("vis-lens", "always-on"): ["always-on"],
}

# Dial-skill map — replaces the registry's ``dial_skill`` leaf plus the
# previously hand-maintained ``_DIAL_SKILL_MAP``. Tracks only families whose
# dial skill's primary purpose is lens/dimension selection. ``arch-lens``
# and ``exp-lens`` dial skills (``prepare-pr`` / ``prepare-research-pr``)
# DO emit ``selected_lenses`` and ``lens_context_paths`` tokens but are
# PR-preparation skills — not lens-selection dial skills.
_DIAL_SKILLS: dict[str, str | None] = {
    "vis-lens": "select-vis-lenses",
    "arch-lens": None,
    "exp-lens": None,
}

_DIAL_SKILL_PAIRS: list[tuple[str, str]] = [
    (family, skill) for family, skill in _DIAL_SKILLS.items() if skill is not None
]

RESEARCH_RECIPE = load_recipe(builtin_recipes_dir() / "research.yaml")
RESEARCH_DESIGN_RECIPE = load_recipe(builtin_recipes_dir() / "research-design.yaml")

_RECIPE_FAMILIES: frozenset[str] = frozenset(
    step.phoropter_family
    for recipe in (RESEARCH_RECIPE, RESEARCH_DESIGN_RECIPE)
    for step in recipe.steps.values()
    if step.phoropter_family and step.phoropter_family in _IMPLEMENTED_FAMILIES
)

# Per-family ``step_naming.prefix`` — derived from
# ``_load_family_prefixes()`` in ``rules_phoropter_adjacency.py`` which is the
# sole production consumer of the registry.
_FAMILY_PREFIX: dict[str, str | None] = {
    "arch-lens": None,
    "exp-lens": None,
    "vis-lens": "vis",
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
    for expected in _IMPLEMENTED_FAMILIES:
        count = sum(1 for family, _ in _LENS_PAIRS if family == expected)
        assert count > 0, f"Discovery found zero {expected}-* lenses under {SKILLS_DIR}"
        assert expected in discovered_families, f"{expected} not found in discovered lens pairs"


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_phoropter_lens_structure(family: str, slug: str) -> None:
    """SKILL.md content contract for every discovered lens.

    Body-derived checks (``## Arguments``, ``context_path``, ``Step 0``,
    ``diagram_path``, ``categories`` frontmatter, ``activate_deps``
    frontmatter, ``experiment_plan_path`` for 2-arg families, vis-lens
    composite marker, vis-lens-methodology-norms special assertions). The
    registry is not consulted at runtime — these checks all derive from
    the SKILL.md file itself.
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
        # vis-lens-methodology-norms carries the special_assertions
        # ``tradition_slug`` and ``two_stage_matching`` (formerly
        # ``lens_metadata.methodology-norms.special_assertions`` in the
        # registry). Body markers must agree.
        assert "tradition_slug" in text, f"{family}-{slug} must document tradition_slug"
        assert "Stage A" in text or "stage A" in text, f"{family}-{slug} must document Stage A"
        assert "Stage B" in text or "stage B" in text, f"{family}-{slug} must document Stage B"
        assert "venue_specific_appendices" in text or "venue appendix" in text, (
            f"{family}-{slug} must document venue_specific_appendices"
        )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_lens_count_derived_from_filesystem(family: str) -> None:
    """Each implemented family's lens count is derived from the filesystem,
    not from the registry.

    ``refactor-lens`` is excluded from ``_IMPLEMENTED_FAMILIES`` because it
    is a designed (not implemented) family with zero lens directories.
    """
    expected_count = sum(
        1 for p in SKILLS_DIR.iterdir() if p.name.startswith(f"{family}-") and p.is_dir()
    )
    assert expected_count > 0, (
        f"Implemented family {family!r} has zero lens directories under {SKILLS_DIR}"
    )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_arg_interface_derived_from_skill_md(family: str, slug: str) -> None:
    """``arg_interface`` is derived from the SKILL.md ``## Arguments`` shape.

    2-arg families declare ``experiment_plan_path`` in their Arguments
    section; 1-arg families do not. The module-level
    ``FAMILY_ARG_INTERFACE`` map is the audit trail.
    """
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
    """``activate_deps`` is read directly from SKILL.md frontmatter.

    All current lenses declare ``["mermaid"]``. If a future lens introduces a
    different dependency, update FAMILY_ACTIVATE_DEPS — but more cleanly,
    just declare it on the lens itself.
    """
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    frontmatter = parse_frontmatter_content(skill_md).data
    deps = (frontmatter or {}).get("activate_deps", [])
    assert deps == ["mermaid"], f"{family}-{slug} activate_deps = {deps}, expected ['mermaid']"


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_composite_slugs_from_body(family: str, slug: str) -> None:
    """``composite_slugs`` is derived from the ``yaml:spec-index`` body marker.

    Asserts agreement between the body marker and the (now-hardcoded)
    composite-slug map; the map itself is the audit trail for which lenses
    are composite.
    """
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    derived_is_composite = "yaml:spec-index" in skill_md
    declared = (family, slug) in _COMPOSITE_SLUGS
    assert derived_is_composite == declared, (
        f"{family}-{slug}: body has yaml:spec-index={derived_is_composite}, "
        f"_COMPOSITE_SLUGS declares composite={declared}"
    )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_output_prefix_from_body(family: str, slug: str) -> None:
    """``output_prefix`` is derived from body text.

    Only vis-lens carries the ``vis_spec_`` output prefix marker.
    """
    skill_md = (SKILLS_DIR / f"{family}-{slug}" / "SKILL.md").read_text()
    if family == "vis-lens":
        assert "vis_spec_" in skill_md, (
            f"{family}-{slug} must reference the vis_spec_ output prefix"
        )


@pytest.mark.parametrize("family,slug", _LENS_PAIRS)
def test_lens_metadata_special_assertions_from_body(family: str, slug: str) -> None:
    """``lens_metadata.special_assertions`` is derived from body markers.

    Currently only ``vis-lens-methodology-norms`` carries both
    ``tradition_slug`` and ``two_stage_matching`` assertions. Body markers
    must agree with the assertion set.
    """
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


def test_collection_does_not_read_registry() -> None:
    """Guard test — verifies that the registry is no longer the structural
    test's source of truth. Two checks:

    1. The registry YAML contains no retired leaves (catches re-accretion
       of dead metadata).
    2. The structural test file itself does not import the registry at
       collection time (catches the silent-pass regression where the
       collection-time ``load_yaml(...)`` is left in place but unused).
    """
    registry_path = pkg_root() / "assets" / "phoropter-registry.yaml"
    registry_text = registry_path.read_text()
    retired_leaves = (
        "arg_interface",
        "activate_deps",
        "composite_slugs",
        "output_prefix",
        "lens_metadata",
        "phase_skip",
        "synthesis",
        "dial_skill",
        "failure_mode",
        "default_enabled",
        "lens_count",
        "mode_label",
        "output_type",
        "description",
    )
    for leaf in retired_leaves:
        assert leaf not in registry_text, (
            f"phoropter-registry.yaml still contains retired leaf {leaf!r}"
        )

    structural_test_path = Path(__file__)
    structural_test_text = structural_test_path.read_text()
    assert "phoropter-registry" not in structural_test_text, (
        f"{structural_test_path.name} still references phoropter-registry.yaml — "
        f"the structural test should derive everything from SKILL.md content"
    )
    assert "_REGISTRY" not in structural_test_text, (
        f"{structural_test_path.name} still has a `_REGISTRY` module-level constant — "
        f"the structural test should not load the registry at import time"
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
