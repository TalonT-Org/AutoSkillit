"""Codex structured provider codes stay aligned with textual classification."""

from __future__ import annotations

import pytest

from autoskillit.execution.session._exit_classification import (
    _CODEX_API_ERROR_PATTERNS,
    _CODEX_ERROR_CODE_API_STATUS,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_codex_error_code_status_mapping_matches_textual_pattern_sources() -> None:
    pattern_sources = {
        pattern.pattern.removeprefix(r"\b").removesuffix(r"\b")
        for pattern in _CODEX_API_ERROR_PATTERNS
    }

    assert set(_CODEX_ERROR_CODE_API_STATUS) == pattern_sources
