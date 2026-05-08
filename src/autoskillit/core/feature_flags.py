"""Feature flag resolution — IL-0 (zero autoskillit imports outside core/).

is_feature_enabled() is the single gating primitive for all feature-gated
code paths. Callers pass config.features (a dict[str, bool]) rather than the
full AutomationConfig to keep core/ free of config/ imports.
"""

from __future__ import annotations

from .types._type_constants import FEATURE_REGISTRY
from .types._type_enums import FeatureLifecycle


def is_feature_enabled(
    name: str,
    features: dict[str, bool],
    *,
    experimental_enabled: bool = False,
) -> bool:
    """Check whether a named feature is enabled.

    Parameters
    ----------
    name:
        Feature name — must exist in FEATURE_REGISTRY. Raises KeyError otherwise.
    features:
        Resolved features dict from ``AutomationConfig.features``. Typically
        passed as ``config.features`` at call sites; never pass the full config.
    experimental_enabled:
        When True, all EXPERIMENTAL lifecycle features are enabled unless explicitly
        overridden by a per-feature entry in ``features``.

    Returns
    -------
    bool
        Resolution order:
        DISABLED hard-off → explicit override → experimental blanket → default_enabled.

    Raises
    ------
    KeyError
        If ``name`` is not a registered feature in FEATURE_REGISTRY.
    """
    defn = FEATURE_REGISTRY.get(name)
    if defn is None:
        raise KeyError(f"Unknown feature: {name!r}")
    if defn.lifecycle == FeatureLifecycle.DISABLED:
        return False
    if name in features:
        return features[name]
    if experimental_enabled and defn.lifecycle == FeatureLifecycle.EXPERIMENTAL:
        return True
    return defn.default_enabled


def _collect_disabled_feature_tags(
    features: dict[str, bool],
    *,
    experimental_enabled: bool = False,
) -> frozenset[str]:
    """Return feature tags that should be suppressed.

    Single source of truth used by _fleet_auto_gate_boot and _redisable_subsets.
    The registry is the sole authority on which tags belong to which feature.
    """
    enabled_tags: set[str] = set()
    disabled_tags: set[str] = set()
    for name, defn in FEATURE_REGISTRY.items():
        if not defn.tool_tags:
            continue
        if is_feature_enabled(name, features, experimental_enabled=experimental_enabled):
            enabled_tags |= defn.tool_tags
        else:
            disabled_tags |= defn.tool_tags
    return frozenset(disabled_tags - enabled_tags)
