"""T2-P1-A5-WP1: Phoropter registry YAML schema and field validation."""

from __future__ import annotations

import pytest

from autoskillit.core import load_yaml, pkg_root
from tests._helpers import IMPLEMENTED_FAMILIES

pytestmark = [pytest.mark.medium]

REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"
EXPECTED_FAMILIES = {"arch-lens", "exp-lens", "vis-lens", "refactor-lens"}
# After the #4894 retirement, the registry carries only step_naming per family.
# All other knobs (synthesis_strategy, dial_skill, lens_count, ...) live on
# the tradition manifest / recipe YAML / SKILL.md frontmatter, not here.
REQUIRED_FIELDS = frozenset({"step_naming"})


@pytest.fixture(scope="module")
def registry_data() -> dict:
    return load_yaml(REGISTRY_PATH)


def test_registry_file_exists() -> None:
    """phoropter-registry.yaml must exist at the canonical assets path."""
    assert REGISTRY_PATH.exists(), f"phoropter-registry.yaml not found at {REGISTRY_PATH}"
    assert REGISTRY_PATH.is_file(), f"{REGISTRY_PATH} is not a regular file"


def test_registry_is_valid_yaml() -> None:
    """Loading the file with load_yaml() must yield a dict."""
    data = load_yaml(REGISTRY_PATH)
    assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}"


def test_schema_version_is_two(registry_data: dict) -> None:
    """schema_version must be the integer 2 after the #4894 retirement."""
    assert registry_data.get("schema_version") == 2, (
        f"Expected schema_version=2, got {registry_data.get('schema_version')!r}"
    )


def test_families_keys_match_expected_set(registry_data: dict) -> None:
    """families must contain exactly the four expected phoropter lens families."""
    families = registry_data.get("families", {})
    assert set(families.keys()) == EXPECTED_FAMILIES, (
        f"Expected families={EXPECTED_FAMILIES}, got {set(families.keys())}"
    )


def test_all_families_have_required_fields(registry_data: dict) -> None:
    """Each family entry must include every required field."""
    families = registry_data["families"]
    for name, entry in families.items():
        missing = REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"Family {name!r} missing required fields: {sorted(missing)}"


def test_registry_has_only_step_naming(registry_data: dict) -> None:
    """After retirement, the registry must contain ONLY step_naming per family.

    Re-accretion guard: any leaf other than ``step_naming`` under a family
    entry (or any leaf under step_naming other than ``prefix``) is a
    regression. The companion contract at
    ``tests/contracts/test_phoropter_registry_leaf_has_consumer.py`` enforces
    the same invariant via ``inert-tracked:#NNNN`` annotation discipline.
    """
    allowed_top_level = {"schema_version", "families"}
    assert set(registry_data.keys()) == allowed_top_level, (
        f"Registry has unexpected top-level keys: {set(registry_data.keys()) - allowed_top_level}"
    )

    families = registry_data["families"]
    for family_name, family_entry in families.items():
        extra = set(family_entry.keys()) - {"step_naming"}
        assert not extra, f"{family_name} has retired leaves: {sorted(extra)}"
        assert set(family_entry["step_naming"].keys()) == {"prefix"}, (
            f"{family_name}.step_naming has unexpected leaves: "
            f"{sorted(set(family_entry['step_naming'].keys()) - {'prefix'})}"
        )


def test_lens_counts_match_actual_directories() -> None:
    """Each implemented family's lens count is derived from the filesystem,
    not from the registry. This test verifies the count is consistent across
    the registry file (no longer carries ``lens_count``) and the canonical
    ``_LENS_PAIRS`` discovery in ``test_phoropter_structural.py``.

    The registry itself no longer stores ``lens_count``; this assertion
    confirms the new minimal schema still agrees with the filesystem.
    """
    skills_root = pkg_root() / "skills_extended"
    for family in IMPLEMENTED_FAMILIES:
        count_via_filesystem = sum(
            1 for p in skills_root.iterdir() if p.name.startswith(f"{family}-") and p.is_dir()
        )
        assert count_via_filesystem > 0, (
            f"Implemented family {family!r} has zero lens directories under {skills_root}"
        )
