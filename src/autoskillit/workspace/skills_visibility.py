"""Internal skill-visibility helpers consumed by the skills facade.

Defines ``_effective_disabled_categories``, ``_skill_is_visible``, and
``_visibility_policy`` — pure-Python functions that translate a
``SkillVisibilitySpec`` into the four-tuple driving the resolver. Exposes
no public surface (``__all__`` is empty); callers reach the helpers through
``autoskillit.workspace.skills``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from autoskillit.core import (
    FEATURE_REGISTRY,
    PACK_REGISTRY,
    FeatureLifecycle,
    SkillVisibilitySpec,
    is_feature_enabled,
)
from autoskillit.workspace.skills_records import SkillInfo


def _effective_disabled_categories(
    *,
    explicit_disabled: Iterable[str],
    packs_enabled: Iterable[str],
    recipe_packs: frozenset[str] | None,
    disabled_feature_tags: frozenset[str],
) -> frozenset[str]:
    """Merge subset, pack, and feature visibility authority."""
    default_disabled = frozenset(
        tag for tag, pack_def in PACK_REGISTRY.items() if not pack_def.default_enabled
    )
    enabled_packs = frozenset(packs_enabled) | (recipe_packs or frozenset())
    return (
        frozenset(explicit_disabled) | (default_disabled - enabled_packs) | disabled_feature_tags
    )


def _skill_is_visible(
    skill: SkillInfo,
    *,
    disabled: frozenset[str],
    custom_tags: Mapping[str, Iterable[str]],
    features: dict[str, bool],
    experimental_enabled: bool,
    allow_only: frozenset[str] | None,
) -> bool:
    """Apply the established subset/pack/feature policy to one effective source."""
    if allow_only is not None and skill.name not in allow_only:
        return False
    allow_only_member = allow_only is not None and skill.name in allow_only
    feature_tool_tags = frozenset(
        tag
        for feature_name, feature_def in FEATURE_REGISTRY.items()
        for tag in feature_def.tool_tags
        if not is_feature_enabled(
            feature_name,
            features,
            experimental_enabled=experimental_enabled,
        )
    )
    for tag in disabled:
        if tag in custom_tags:
            if skill.name in custom_tags[tag]:
                return False
        elif tag in skill.categories:
            if allow_only_member and tag in feature_tool_tags:
                continue
            return False

    enabled_categories: set[str] = set()
    disabled_categories: set[str] = set()
    for feature_name, feature_def in FEATURE_REGISTRY.items():
        if is_feature_enabled(
            feature_name,
            features,
            experimental_enabled=experimental_enabled,
        ):
            enabled_categories.update(feature_def.skill_categories)
        else:
            disabled_categories.update(feature_def.skill_categories)
    gated_categories = disabled_categories - enabled_categories
    return allow_only_member or not bool(skill.categories & gated_categories)


def _visibility_policy(
    visibility: SkillVisibilitySpec | None,
    *,
    cook_session: bool,
    recipe_packs: frozenset[str] | None,
    recipe_features: frozenset[str] | None,
) -> tuple[
    frozenset[str],
    Mapping[str, Iterable[str]],
    dict[str, bool],
    bool,
]:
    """Resolve effective visibility from the core-owned policy contract."""
    if cook_session:
        explicit_disabled: Iterable[str] = ()
        custom_tags: Mapping[str, Iterable[str]] = {}
        features: dict[str, bool] = {
            name: True
            for name, definition in FEATURE_REGISTRY.items()
            if definition.lifecycle is not FeatureLifecycle.DISABLED
        }
        experimental_enabled = False
    elif visibility is None:
        explicit_disabled = ()
        custom_tags = {}
        features = {}
        experimental_enabled = False
    else:
        explicit_disabled = visibility.disabled_categories
        custom_tags = visibility.custom_tags
        features = dict(visibility.features)
        experimental_enabled = visibility.experimental_enabled

    if recipe_features and not cook_session:
        for feature_name in recipe_features:
            if feature_name in FEATURE_REGISTRY and feature_name not in features:
                features[feature_name] = True

    disabled_feature_tags: frozenset[str] = frozenset()
    if not cook_session:
        enabled_tool_tags: set[str] = set()
        disabled_tool_tags: set[str] = set()
        for feature_name, feature_def in FEATURE_REGISTRY.items():
            if is_feature_enabled(
                feature_name,
                features,
                experimental_enabled=experimental_enabled,
            ):
                enabled_tool_tags.update(feature_def.tool_tags)
            else:
                disabled_tool_tags.update(feature_def.tool_tags)
        disabled_feature_tags = frozenset(disabled_tool_tags - enabled_tool_tags)

    packs_enabled = () if visibility is None else visibility.enabled_packs
    disabled = _effective_disabled_categories(
        explicit_disabled=explicit_disabled,
        packs_enabled=packs_enabled,
        recipe_packs=recipe_packs,
        disabled_feature_tags=disabled_feature_tags,
    )
    return disabled, custom_tags, features, experimental_enabled


__all__: list[
    str
] = []  # Internal shard — visibility policy helpers reached via the skills facade.
