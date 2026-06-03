"""Shared alias registry consistency tests."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

VALID_CODEX_MODEL_IDS: frozenset[str] = frozenset({"gpt-5.5"})

VALID_CLAUDE_MODEL_IDS: frozenset[str] = frozenset({"sonnet", "opus", "haiku"})


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


def test_codex_alias_values_in_allowlist() -> None:
    from autoskillit.core.types._type_backend import CODEX_MODEL_ALIASES

    for key, value in CODEX_MODEL_ALIASES.items():
        assert value in VALID_CODEX_MODEL_IDS, (
            f"CODEX_MODEL_ALIASES[{key!r}] = {value!r} is not in VALID_CODEX_MODEL_IDS. "
            "Update VALID_CODEX_MODEL_IDS if the intended target model changed."
        )


def test_claude_alias_values_in_allowlist() -> None:
    from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES

    for key, value in CLAUDE_MODEL_ALIASES.items():
        assert value in VALID_CLAUDE_MODEL_IDS, (
            f"CLAUDE_MODEL_ALIASES[{key!r}] = {value!r} is not in VALID_CLAUDE_MODEL_IDS. "
            "Update VALID_CLAUDE_MODEL_IDS if the intended target model changed."
        )


def test_codex_alias_values_differ_from_keys() -> None:
    from autoskillit.core.types._type_backend import CODEX_MODEL_ALIASES

    for key, value in CODEX_MODEL_ALIASES.items():
        assert value != key, (
            f"CODEX_MODEL_ALIASES[{key!r}] == {value!r}: identity mapping passes raw Anthropic "
            "alias to Codex CLI. Codex requires real OpenAI model IDs."
        )
