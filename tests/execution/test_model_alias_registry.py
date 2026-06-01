"""Shared alias registry consistency tests."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_anomaly_detection_aliases_keys_match_shared() -> None:
    from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES, CODEX_MODEL_ALIASES
    from autoskillit.execution.anomaly_detection import _MODEL_SHORT_ALIASES

    for key in _MODEL_SHORT_ALIASES:
        assert key in CLAUDE_MODEL_ALIASES, (
            f"Alias key '{key}' in anomaly_detection._MODEL_SHORT_ALIASES "
            f"but missing from CLAUDE_MODEL_ALIASES"
        )
        assert key in CODEX_MODEL_ALIASES, (
            f"Alias key '{key}' in anomaly_detection._MODEL_SHORT_ALIASES "
            f"but missing from CODEX_MODEL_ALIASES"
        )
