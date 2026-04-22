"""Feature registry structural and behavioral self-tests."""

from __future__ import annotations

import importlib
from datetime import date

import pytest

# ── Structural registry tests ─────────────────────────────────────────────────


def test_feature_lifecycle_enum_exists():
    """FeatureLifecycle StrEnum exists with 3 members."""
    from autoskillit.core._type_enums import FeatureLifecycle

    assert set(FeatureLifecycle) == {
        FeatureLifecycle.EXPERIMENTAL,
        FeatureLifecycle.STABLE,
        FeatureLifecycle.DEPRECATED,
    }


def test_feature_registry_keys_are_sorted():
    """FEATURE_REGISTRY keys must be alphabetically sorted (prevents merge conflicts)."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    keys = list(FEATURE_REGISTRY.keys())
    assert keys == sorted(keys), f"FEATURE_REGISTRY keys not sorted: {keys}"


def test_feature_tool_tags_exist_in_subset_tags():
    """Every FeatureDef.tool_tags entry exists in TOOL_SUBSET_TAGS tag values."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY, TOOL_SUBSET_TAGS

    all_tags = frozenset(tag for tags in TOOL_SUBSET_TAGS.values() for tag in tags)
    violations = [
        f"{defn.name}.tool_tags contains {tag!r} not in TOOL_SUBSET_TAGS"
        for defn in FEATURE_REGISTRY.values()
        for tag in defn.tool_tags
        if tag not in all_tags
    ]
    assert not violations, "\n".join(violations)


def test_feature_import_package_exists():
    """Every FeatureDef.import_package resolves to a real importable package."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    failures = []
    for defn in FEATURE_REGISTRY.values():
        if defn.import_package is None:
            continue
        try:
            importlib.import_module(defn.import_package)
        except ImportError as e:
            failures.append(f"{defn.name}.import_package={defn.import_package!r}: {e}")
    assert not failures, "\n".join(failures)


def test_no_retired_feature_has_live_registry_entry():
    """RETIRED_FEATURES and FEATURE_REGISTRY must be disjoint."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY, RETIRED_FEATURES

    overlap = RETIRED_FEATURES & frozenset(FEATURE_REGISTRY.keys())
    assert not overlap, f"Names appear in both RETIRED_FEATURES and FEATURE_REGISTRY: {overlap}"


def test_stable_features_are_default_enabled():
    """lifecycle=STABLE implies default_enabled=True."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core._type_enums import FeatureLifecycle

    violations = [
        defn.name
        for defn in FEATURE_REGISTRY.values()
        if defn.lifecycle == FeatureLifecycle.STABLE and not defn.default_enabled
    ]
    assert not violations, f"STABLE features must be default_enabled=True: {violations}"


def test_sunset_dates_not_expired():
    """Time-bomb: no FeatureDef may have a sunset_date in the past."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    today = date.today()
    expired = [
        f"{defn.name} (sunset={defn.sunset_date})"
        for defn in FEATURE_REGISTRY.values()
        if defn.sunset_date is not None and defn.sunset_date < today
    ]
    assert not expired, f"Features with expired sunset_date: {expired}"


def test_feature_depends_on_references_valid_features():
    """All depends_on entries must reference names that exist in FEATURE_REGISTRY."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    violations = [
        f"{defn.name}.depends_on contains unknown {dep!r}"
        for defn in FEATURE_REGISTRY.values()
        for dep in defn.depends_on
        if dep not in FEATURE_REGISTRY
    ]
    assert not violations, "\n".join(violations)


def test_feature_skill_categories_match_real_skills():
    """Every FeatureDef.skill_categories entry maps to at least one SKILL.md."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core.paths import pkg_root

    skills_dirs = [pkg_root() / "skills", pkg_root() / "skills_extended"]
    # Build a set of skill directory names as proxy for categories
    all_category_tags: set[str] = set()
    for skills_dir in skills_dirs:
        if not skills_dir.exists():
            continue
        for skill_md in skills_dir.rglob("SKILL.md"):
            all_category_tags.add(skill_md.parent.name)

    violations = [
        f"{defn.name}.skill_categories contains {cat!r}: no matching SKILL.md found"
        for defn in FEATURE_REGISTRY.values()
        for cat in defn.skill_categories
        if cat not in all_category_tags
    ]
    assert not violations, "\n".join(violations)


# ── is_feature_enabled() behavioral tests ────────────────────────────────────


def test_is_feature_enabled_defaults():
    """is_feature_enabled uses FeatureDef.default_enabled when key absent from dict."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core.feature_flags import is_feature_enabled

    for name, defn in FEATURE_REGISTRY.items():
        assert is_feature_enabled(name, {}) == defn.default_enabled, (
            f"{name}: is_feature_enabled({name!r}, {{}}) should be {defn.default_enabled}"
        )


def test_is_feature_enabled_override():
    """is_feature_enabled respects explicit overrides in the features dict."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core.feature_flags import is_feature_enabled

    assert len(FEATURE_REGISTRY) > 0, "FEATURE_REGISTRY must not be empty"
    for name in FEATURE_REGISTRY:
        assert is_feature_enabled(name, {name: True}) is True
        assert is_feature_enabled(name, {name: False}) is False


def test_is_feature_enabled_unknown():
    """is_feature_enabled raises KeyError for an unknown feature name."""
    from autoskillit.core.feature_flags import is_feature_enabled

    with pytest.raises(KeyError, match="unknown_feature_xyz"):
        is_feature_enabled("unknown_feature_xyz", {})


# ── Config integration tests ──────────────────────────────────────────────────


def test_config_rejects_unknown_feature():
    """_build_features_dict raises ConfigSchemaError for keys not in FEATURE_REGISTRY."""
    from autoskillit.config.settings import AutomationConfig, ConfigSchemaError
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    unknown = "this_feature_does_not_exist_xyz"
    assert unknown not in FEATURE_REGISTRY, "Test setup error: pick a truly unknown name"

    with pytest.raises(ConfigSchemaError, match=unknown):
        AutomationConfig._build_features_dict({unknown: True})


def test_config_dependency_validation(monkeypatch):
    """_build_features_dict raises ConfigSchemaError when B is enabled but dep A is disabled."""

    from autoskillit.config.settings import AutomationConfig, ConfigSchemaError
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle

    # Temporarily patch FEATURE_REGISTRY with a dep-requiring entry for this test
    dep_feature = FeatureDef(
        name="test_dep_b",
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="test dep B",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
        depends_on=frozenset({"test_dep_a"}),
    )
    dep_parent = FeatureDef(
        name="test_dep_a",
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="test dep A",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
    )
    import autoskillit.core._type_constants as tc

    monkeypatch.setitem(tc.FEATURE_REGISTRY, "test_dep_a", dep_parent)
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "test_dep_b", dep_feature)
    # B enabled without A → ConfigSchemaError
    with pytest.raises(ConfigSchemaError):
        AutomationConfig._build_features_dict({"test_dep_b": True, "test_dep_a": False})
    # B enabled with A → no error
    AutomationConfig._build_features_dict({"test_dep_b": True, "test_dep_a": True})


def test_no_unregistered_feature_tag_on_tools():
    """No tool in TOOL_SUBSET_TAGS may carry a tag absent from all known registries.

    Known registries: FEATURE_REGISTRY names, PACK_REGISTRY names, and known
    non-feature structural tags (e.g. 'kitchen-core').
    """
    from autoskillit.core._type_constants import (
        FEATURE_REGISTRY,
        PACK_REGISTRY,
        TOOL_SUBSET_TAGS,
    )

    # Known structural (non-feature, non-pack) tags that are always valid
    STRUCTURAL_TAGS: frozenset[str] = frozenset({"kitchen-core"})

    known = frozenset(FEATURE_REGISTRY.keys()) | frozenset(PACK_REGISTRY.keys()) | STRUCTURAL_TAGS
    violations = [
        f"Tool {tool!r} has tag {tag!r} not in FEATURE_REGISTRY, PACK_REGISTRY, or STRUCTURAL_TAGS"
        for tool, tags in TOOL_SUBSET_TAGS.items()
        for tag in tags
        if tag not in known
    ]
    assert not violations, "\n".join(violations)
