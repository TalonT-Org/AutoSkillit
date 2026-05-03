"""Architectural invariant tests for registry key casing."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_feature_registry_keys_are_lowercase():
    """All FEATURE_REGISTRY keys must be lowercase."""
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    offending = sorted(k for k in FEATURE_REGISTRY if k != k.lower())
    assert not offending, f"FEATURE_REGISTRY has non-lowercase keys: {offending}"


def test_retired_features_entries_are_lowercase():
    """All RETIRED_FEATURES entries must be lowercase."""
    from autoskillit.core._type_constants import RETIRED_FEATURES

    offending = sorted(k for k in RETIRED_FEATURES if k != k.lower())
    assert not offending, f"RETIRED_FEATURES has non-lowercase entries: {offending}"


def test_pack_registry_keys_are_lowercase():
    """All PACK_REGISTRY keys must be lowercase."""
    from autoskillit.core._type_constants import PACK_REGISTRY

    offending = sorted(k for k in PACK_REGISTRY if k != k.lower())
    assert not offending, f"PACK_REGISTRY has non-lowercase keys: {offending}"


def test_recipe_pack_registry_keys_are_lowercase():
    """All RECIPE_PACK_REGISTRY keys must be lowercase."""
    from autoskillit.core._type_constants import RECIPE_PACK_REGISTRY

    offending = sorted(k for k in RECIPE_PACK_REGISTRY if k != k.lower())
    assert not offending, f"RECIPE_PACK_REGISTRY has non-lowercase keys: {offending}"
