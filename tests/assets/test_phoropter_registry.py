"""T2-P1-A5-WP1: Phoropter registry YAML schema and field validation."""

from __future__ import annotations

import pytest

from autoskillit.core import load_yaml, pkg_root

pytestmark = [pytest.mark.medium]

REGISTRY_PATH = pkg_root() / "assets" / "phoropter-registry.yaml"
EXPECTED_FAMILIES = {"arch-lens", "exp-lens", "vis-lens", "refactor-lens"}
REQUIRED_FIELDS = frozenset(
    {
        "description",
        "output_type",
        "mode_label",
        "lens_count",
        "default_enabled",
        "failure_mode",
        "arg_interface",
        "dial_skill",
        "synthesis",
        "step_naming",
        "status",
    }
)


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


def test_schema_version_is_one(registry_data: dict) -> None:
    """schema_version must be the integer 1 (not the string '1')."""
    assert registry_data.get("schema_version") == 1, (
        f"Expected schema_version=1, got {registry_data.get('schema_version')!r}"
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


def test_arch_lens_fields(registry_data: dict) -> None:
    """arch-lens entry must match the documented field values."""
    entry = registry_data["families"]["arch-lens"]
    assert entry["output_type"] == "diagram"
    assert entry["lens_count"] == 13
    assert entry["arg_interface"] == "1-arg"
    assert entry["dial_skill"] == "prepare-pr"
    assert entry["synthesis"]["strategy"] is None
    assert entry["step_naming"]["prefix"] is None
    assert entry["status"] == "implemented"
    assert entry["default_enabled"] is True
    assert entry["failure_mode"] == "continue"
    assert "phase_skip" not in entry


def test_exp_lens_fields(registry_data: dict) -> None:
    """exp-lens entry must match the documented field values."""
    entry = registry_data["families"]["exp-lens"]
    assert entry["output_type"] == "assessment"
    assert entry["lens_count"] == 18
    assert entry["arg_interface"] == "2-arg"
    assert entry["dial_skill"] == "prepare-research-pr"
    assert entry["synthesis"]["strategy"] == "priority_hierarchy"
    assert "skill" not in entry["synthesis"]
    assert entry["step_naming"]["prefix"] is None
    assert entry["status"] == "implemented"
    assert "phase_skip" not in entry


def test_vis_lens_fields(registry_data: dict) -> None:
    """vis-lens entry must match the documented field values."""
    entry = registry_data["families"]["vis-lens"]
    assert entry["output_type"] == "figure_spec"
    assert entry["lens_count"] == 12
    assert entry["arg_interface"] == "2-arg"
    assert entry["dial_skill"] == "select-vis-lenses"
    assert entry["synthesis"]["strategy"] == "priority_hierarchy"
    assert entry["synthesis"]["skill"] == "synthesize-vis-plan"
    assert entry["step_naming"]["prefix"] == "vis"
    assert entry["status"] == "implemented"
    assert entry["phase_skip"]["skip_field"] == "context.is_silent_type"
    assert entry["phase_skip"]["skip_semantics"] == "skip_when_true"
    assert entry["phase_skip"]["applies_to"] == "apply"
    assert "lens_metadata" in entry
    assert isinstance(entry["lens_metadata"], dict)


def test_refactor_lens_fields(registry_data: dict) -> None:
    """refactor-lens entry must match the documented field values."""
    entry = registry_data["families"]["refactor-lens"]
    assert entry["lens_count"] == 0
    assert entry["default_enabled"] is False
    assert entry["synthesis"]["strategy"] == "electre_iii"
    assert entry["step_naming"]["prefix"] == "refactor"
    assert entry["status"] == "designed"
    assert entry["dial_skill"] is None
    assert "phase_skip" not in entry


def test_dial_skill_present_on_every_family(registry_data: dict) -> None:
    """Every family must declare dial_skill (value may be None for unimplemented families)."""
    families = registry_data["families"]
    for name, entry in families.items():
        assert "dial_skill" in entry, f"Family {name!r} missing dial_skill key"


def test_lens_counts_match_actual_directories(registry_data: dict) -> None:
    """For implemented families, the directory glob count must equal the registry's lens_count."""
    families = registry_data["families"]
    skills_root = pkg_root() / "skills_extended"
    for slug in ("arch-lens", "exp-lens", "vis-lens"):
        entry = families[slug]
        matches = list(skills_root.glob(f"{slug}-*"))
        assert len(matches) == entry["lens_count"], (
            f"{slug}: registry claims {entry['lens_count']} lenses but "
            f"{skills_root} contains {len(matches)} matching directories"
        )
