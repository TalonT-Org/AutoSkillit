"""Feature registry structural and behavioral self-tests."""

from __future__ import annotations

import importlib
from datetime import date

import pytest

# ── Structural registry tests ─────────────────────────────────────────────────


def test_feature_lifecycle_enum_exists():
    """FeatureLifecycle StrEnum exists with 4 members."""
    from autoskillit.core._type_enums import FeatureLifecycle

    assert set(FeatureLifecycle) == {
        FeatureLifecycle.EXPERIMENTAL,
        FeatureLifecycle.STABLE,
        FeatureLifecycle.DEPRECATED,
        FeatureLifecycle.DISABLED,
    }


def test_feature_registry_keys_are_sorted():
    """FEATURE_REGISTRY keys must be alphabetically sorted (prevents merge conflicts)."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    keys = list(FEATURE_REGISTRY.keys())
    assert keys == sorted(keys), f"FEATURE_REGISTRY keys not sorted: {keys}"


def test_feature_registry_key_name_consistency() -> None:
    """FEATURE_REGISTRY key must equal FeatureDef.name for every entry."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    mismatches = {k: defn.name for k, defn in FEATURE_REGISTRY.items() if k != defn.name}
    assert not mismatches, f"FEATURE_REGISTRY key/name mismatch: {mismatches}"


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
    """Every FeatureDef.skill_categories entry must map to a frontmatter category tag."""
    import yaml

    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core.paths import pkg_root

    skills_dirs = [pkg_root() / "skills", pkg_root() / "skills_extended"]
    all_category_tags: set[str] = set()
    for skills_dir in skills_dirs:
        if not skills_dir.exists():
            continue
        for skill_md in skills_dir.rglob("SKILL.md"):
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not content.startswith("---"):
                continue
            parts = content.split("---", 2)
            if len(parts) < 3:
                continue
            try:
                data = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                continue
            cats = data.get("categories", [])
            if isinstance(cats, list):
                all_category_tags.update(str(c) for c in cats)

    violations = [
        (
            f"{defn.name}.skill_categories contains {cat!r}:"
            " no skill declares this category in frontmatter"
        )
        for defn in FEATURE_REGISTRY.values()
        for cat in defn.skill_categories
        if cat not in all_category_tags
    ]
    assert not violations, "\n".join(violations)


# ── is_feature_enabled() behavioral tests ────────────────────────────────────


def test_is_feature_enabled_defaults():
    """is_feature_enabled uses FeatureDef.default_enabled when experimental_enabled=False."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core._type_enums import FeatureLifecycle
    from autoskillit.core.feature_flags import is_feature_enabled

    for name, defn in FEATURE_REGISTRY.items():
        expected = False if defn.lifecycle == FeatureLifecycle.DISABLED else defn.default_enabled
        result = is_feature_enabled(name, {}, experimental_enabled=False)
        assert result == expected, (
            f"{name}: is_feature_enabled({name!r}, {{}}, experimental_enabled=False)"
            f" should be {expected}"
        )


def test_is_feature_enabled_override():
    """is_feature_enabled respects explicit overrides in the features dict (except DISABLED)."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY
    from autoskillit.core._type_enums import FeatureLifecycle
    from autoskillit.core.feature_flags import is_feature_enabled

    assert len(FEATURE_REGISTRY) > 0, "FEATURE_REGISTRY must not be empty"
    for name, defn in FEATURE_REGISTRY.items():
        if defn.lifecycle == FeatureLifecycle.DISABLED:
            # DISABLED cannot be enabled by any override
            assert is_feature_enabled(name, {name: True}) is False
            assert is_feature_enabled(name, {name: False}) is False
        else:
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
    result, _ = AutomationConfig._build_features_dict({"test_dep_b": True, "test_dep_a": True})
    assert result["test_dep_b"] is True


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
    STRUCTURAL_TAGS: frozenset[str] = frozenset({"kitchen-core", "fleet-dispatch"})

    known = frozenset(FEATURE_REGISTRY.keys()) | frozenset(PACK_REGISTRY.keys()) | STRUCTURAL_TAGS
    violations = [
        f"Tool {tool!r} has tag {tag!r} not in FEATURE_REGISTRY, PACK_REGISTRY, or STRUCTURAL_TAGS"
        for tool, tags in TOOL_SUBSET_TAGS.items()
        for tag in tags
        if tag not in known
    ]
    assert not violations, "\n".join(violations)


# ── Fleet feature registry tests — T1 shims ──────────────────────────────────


def test_fleet_in_feature_registry():
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    assert "fleet" in FEATURE_REGISTRY


def test_fleet_feature_def_name_matches_key():
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].name == "fleet"


def test_fleet_feature_tool_tags_in_tool_subset_tags():
    from autoskillit.core._type_constants import FEATURE_REGISTRY, TOOL_SUBSET_TAGS

    all_tags = set().union(*TOOL_SUBSET_TAGS.values())
    for tag in FEATURE_REGISTRY["fleet"].tool_tags:
        assert tag in all_tags, f"tag {tag!r} from fleet FeatureDef not in TOOL_SUBSET_TAGS"


def test_fleet_feature_default_disabled():
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].default_enabled is False


def test_build_features_dict_accepts_fleet_key():
    from autoskillit.config.settings import AutomationConfig

    result, exp_enabled = AutomationConfig._build_features_dict({"fleet": True})
    assert result == {"fleet": True}
    assert exp_enabled is False


def test_build_features_dict_franchise_raises_config_schema_error():
    """T2: 'franchise' alias removed — _build_features_dict raises ConfigSchemaError."""
    from autoskillit.config.settings import AutomationConfig, ConfigSchemaError

    with pytest.raises(ConfigSchemaError, match="franchise"):
        AutomationConfig._build_features_dict({"franchise": True})


# ── T1: DISABLED lifecycle ──────────────────────────────────────────────────


def test_is_feature_enabled_disabled_lifecycle_always_false(monkeypatch):
    """DISABLED lifecycle features return False regardless of config or experimental_enabled."""
    import autoskillit.core._type_constants as tc
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle
    from autoskillit.core.feature_flags import is_feature_enabled

    disabled_def = FeatureDef(
        name="test_disabled_feat",
        lifecycle=FeatureLifecycle.DISABLED,
        description="disabled test feature",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
    )
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "test_disabled_feat", disabled_def)
    assert is_feature_enabled("test_disabled_feat", {}, experimental_enabled=False) is False
    assert (
        is_feature_enabled(
            "test_disabled_feat", {"test_disabled_feat": True}, experimental_enabled=True
        )
        is False
    )


def test_build_features_dict_rejects_enabling_disabled_feature(monkeypatch):
    """_build_features_dict raises ConfigSchemaError if a DISABLED feature is set to True."""
    import autoskillit.core._type_constants as tc
    from autoskillit.config.settings import AutomationConfig, ConfigSchemaError
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle

    disabled_def = FeatureDef(
        name="test_cannot_enable",
        lifecycle=FeatureLifecycle.DISABLED,
        description="cannot enable",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
    )
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "test_cannot_enable", disabled_def)
    with pytest.raises(ConfigSchemaError, match="DISABLED"):
        AutomationConfig._build_features_dict({"test_cannot_enable": True})
    # Setting to False is fine
    result, _ = AutomationConfig._build_features_dict({"test_cannot_enable": False})
    assert result["test_cannot_enable"] is False


# ── T2: experimental_enabled blanket toggle ────────────────────────────────


def test_is_feature_enabled_experimental_blanket(monkeypatch):
    """EXPERIMENTAL feature is True when experimental_enabled=True and no override."""
    import autoskillit.core._type_constants as tc
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle
    from autoskillit.core.feature_flags import is_feature_enabled

    exp_def = FeatureDef(
        name="test_exp_feat",
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="experimental",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
        default_enabled=False,
    )
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "test_exp_feat", exp_def)
    assert is_feature_enabled("test_exp_feat", {}, experimental_enabled=False) is False
    assert is_feature_enabled("test_exp_feat", {}, experimental_enabled=True) is True
    assert (
        is_feature_enabled("test_exp_feat", {"test_exp_feat": False}, experimental_enabled=True)
        is False
    )


def test_is_feature_enabled_stable_unaffected_by_experimental_enabled(monkeypatch):
    """experimental_enabled has no effect on STABLE features."""
    import autoskillit.core._type_constants as tc
    from autoskillit.core._type_constants import FeatureDef
    from autoskillit.core._type_enums import FeatureLifecycle
    from autoskillit.core.feature_flags import is_feature_enabled

    stable_def = FeatureDef(
        name="test_stable_feat",
        lifecycle=FeatureLifecycle.STABLE,
        description="stable",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
        default_enabled=True,
    )
    monkeypatch.setitem(tc.FEATURE_REGISTRY, "test_stable_feat", stable_def)
    assert is_feature_enabled("test_stable_feat", {}, experimental_enabled=True) is True
    assert is_feature_enabled("test_stable_feat", {}, experimental_enabled=False) is True


# ── T3: AutomationConfig.experimental_enabled field ────────────────────────


def test_automation_config_experimental_enabled_field():
    """AutomationConfig has experimental_enabled: bool defaulting to False."""
    from autoskillit.config.settings import AutomationConfig

    cfg = AutomationConfig()
    assert hasattr(cfg, "experimental_enabled")
    assert cfg.experimental_enabled is False


def test_build_features_dict_extracts_experimental_enabled():
    """_build_features_dict returns (dict, bool) and strips experimental_enabled from dict."""
    from autoskillit.config.settings import AutomationConfig

    result, exp_enabled = AutomationConfig._build_features_dict({"experimental_enabled": True})
    assert exp_enabled is True
    assert "experimental_enabled" not in result


def test_build_features_dict_experimental_enabled_defaults_false():
    """_build_features_dict returns experimental_enabled=False when key absent."""
    from autoskillit.config.settings import AutomationConfig

    result, exp_enabled = AutomationConfig._build_features_dict({})
    assert exp_enabled is False
    assert result == {}


def test_build_config_schema_accepts_experimental_enabled():
    """validate_layer_keys does not raise for features.experimental_enabled."""
    from autoskillit.config.settings import validate_layer_keys

    validate_layer_keys(
        {"features": {"experimental_enabled": True}},
        layer_path="test",
        is_secrets_layer=False,
    )


# ── T4: defaults.yaml ships experimental_enabled: true ─────────────────────


def test_defaults_yaml_has_experimental_enabled_true():
    """Package defaults.yaml sets experimental_enabled=true; no per-feature entries."""
    import yaml

    from autoskillit.core.paths import pkg_root

    defaults = yaml.safe_load((pkg_root() / "config" / "defaults.yaml").read_text())
    features = defaults.get("features", {})
    assert features.get("experimental_enabled") is True
    assert "fleet" not in features, "fleet entry removed from defaults.yaml"
    assert "planner" not in features, "planner entry removed from defaults.yaml"


def test_load_config_integration_experimental_enabled(tmp_path):
    """load_config resolves cfg.experimental_enabled=True from defaults.yaml."""
    import os
    from unittest.mock import patch

    from autoskillit.config.settings import load_config

    with patch.dict(os.environ, {}, clear=False):
        cfg = load_config(tmp_path)
    assert cfg.experimental_enabled is True
