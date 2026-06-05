from __future__ import annotations

import pytest

from autoskillit.core import load_yaml, pkg_root

SKILLS_DIR = pkg_root() / "skills_extended"

_REGISTRY = load_yaml(pkg_root() / "assets" / "phoropter-registry.yaml")

_LENS_PAIRS: list[tuple[str, str]] = sorted(
    (family, child.name[len(family) + 1 :])
    for family in _REGISTRY["families"]
    for child in sorted(SKILLS_DIR.iterdir())
    if child.is_dir() and child.name.startswith(f"{family}-")
)

_IMPLEMENTED_FAMILIES = frozenset({"vis-lens", "arch-lens", "exp-lens"})

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def test_discovery_is_non_empty_and_covers_all_families() -> None:
    assert len(_LENS_PAIRS) >= 43
    discovered_families = {family for family, _ in _LENS_PAIRS}
    for expected in _IMPLEMENTED_FAMILIES:
        assert expected in discovered_families, f"{expected} not found in discovered lens pairs"
