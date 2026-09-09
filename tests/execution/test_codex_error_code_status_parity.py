"""Codex structured provider codes stay aligned with textual classification."""

from __future__ import annotations

import pytest

from autoskillit.execution.session._exit_classification import (
    _CODEX_API_ERROR_PATTERNS,
    _CODEX_ERROR_CODE_API_STATUS,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_codex_error_code_status_mapping_matches_textual_pattern_sources() -> None:
    # Structural containment instead of a lossy \b-strip-and-compare: every mapped
    # code must be matched by some pattern, and every pattern must contain some
    # mapped code as a literal substring. This holds regardless of what boundary
    # syntax (\b, ^, $, or none) a pattern uses, unlike stripping only a leading/
    # trailing literal "\b" from the pattern text.
    for code in _CODEX_ERROR_CODE_API_STATUS:
        assert any(pattern.search(code) for pattern in _CODEX_API_ERROR_PATTERNS), (
            f"{code!r} is not matched by any pattern in _CODEX_API_ERROR_PATTERNS"
        )
    for pattern in _CODEX_API_ERROR_PATTERNS:
        assert any(code in pattern.pattern for code in _CODEX_ERROR_CODE_API_STATUS), (
            f"{pattern.pattern!r} does not contain any code from _CODEX_ERROR_CODE_API_STATUS"
        )
