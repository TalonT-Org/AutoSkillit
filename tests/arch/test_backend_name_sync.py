"""Ensure KNOWN_BACKEND_NAMES stays in sync with BACKEND_REGISTRY."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_known_backend_names_matches_registry():
    """KNOWN_BACKEND_NAMES (IL-0) must equal BACKEND_REGISTRY keys (IL-1)."""
    from autoskillit.core import KNOWN_BACKEND_NAMES
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert KNOWN_BACKEND_NAMES == frozenset(BACKEND_REGISTRY), (
        f"KNOWN_BACKEND_NAMES={sorted(KNOWN_BACKEND_NAMES)} does not match "
        f"BACKEND_REGISTRY keys={sorted(BACKEND_REGISTRY)}. "
        "Update KNOWN_BACKEND_NAMES in core/types/_type_constants_env.py."
    )
