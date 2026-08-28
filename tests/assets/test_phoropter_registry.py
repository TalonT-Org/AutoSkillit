"""T2-P1-A5-WP1: Phoropter registry YAML schema and field validation."""

from __future__ import annotations

import pytest

from autoskillit.core import load_yaml, pkg_root
from tests._helpers import IMPLEMENTED_FAMILIES

pytestmark = [pytest.mark.medium]

REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"
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


def test_implemented_families_are_present(registry_data: dict) -> None:
    """Every implemented family must have an entry in the registry."""
    families = registry_data.get("families", {})
    missing = IMPLEMENTED_FAMILIES - set(families.keys())
    assert not missing, f"Implemented families missing from registry: {sorted(missing)}"


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


def test_each_implemented_family_has_lenses() -> None:
    """Each implemented family must have at least one lens directory.

    Lens counts are derived from the filesystem; the registry no longer
    stores them post-#4894.
    """
    skills_root = pkg_root() / "skills_extended"
    for family in IMPLEMENTED_FAMILIES:
        count = sum(
            1 for p in skills_root.iterdir() if p.name.startswith(f"{family}-") and p.is_dir()
        )
        assert count > 0, (
            f"Implemented family {family!r} has zero lens directories under {skills_root}"
        )
