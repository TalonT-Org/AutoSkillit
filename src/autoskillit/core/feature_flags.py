"""Feature flag resolution — L0 (zero autoskillit imports outside core/).

is_feature_enabled() is the single gating primitive for all feature-gated
code paths. Callers pass config.features (a dict[str, bool]) rather than the
full AutomationConfig to keep core/ free of config/ imports.
"""

from __future__ import annotations

from ._type_constants import FEATURE_REGISTRY


def is_feature_enabled(name: str, features: dict[str, bool]) -> bool:
    """Check whether a named feature is enabled.

    Parameters
    ----------
    name:
        Feature name — must exist in FEATURE_REGISTRY. Raises KeyError otherwise.
    features:
        Resolved features dict from ``AutomationConfig.features``. Typically
        passed as ``config.features`` at call sites; never pass the full config.

    Returns
    -------
    bool
        ``features[name]`` if the key is present; otherwise ``FeatureDef.default_enabled``.

    Raises
    ------
    KeyError
        If ``name`` is not a registered feature in FEATURE_REGISTRY.
    """
    defn = FEATURE_REGISTRY.get(name)
    if defn is None:
        raise KeyError(f"Unknown feature: {name!r}")
    return features.get(name, defn.default_enabled)
