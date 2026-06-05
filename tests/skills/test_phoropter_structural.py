from __future__ import annotations

import pytest

from autoskillit.core import load_yaml, pkg_root
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
