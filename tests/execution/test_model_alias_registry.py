"""Shared alias registry consistency tests."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

VALID_CLAUDE_MODEL_IDS: frozenset[str] = frozenset({"claude-sonnet-5", "opus", "haiku"})


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
    from autoskillit.core.types._type_backend import CODEX_MODEL_ALIASES, is_valid_codex_model_id

    for key, value in CODEX_MODEL_ALIASES.items():
        assert is_valid_codex_model_id(value), (
            f"CODEX_MODEL_ALIASES[{key!r}] = {value!r} is not a valid Codex model ID. "
            "Update CODEX_VALID_MODEL_IDS if the intended target model changed."
        )


def test_codex_aliases_map_classes_to_tiers() -> None:
    from autoskillit.core.types._type_backend import CODEX_MODEL_ALIASES

    assert set(CODEX_MODEL_ALIASES) == {"sonnet", "opus", "haiku"}
    assert CODEX_MODEL_ALIASES["sonnet"] == "gpt-5.6-sol"
    assert CODEX_MODEL_ALIASES["opus"] == "gpt-5.6-sol"
    assert CODEX_MODEL_ALIASES["haiku"] == "gpt-5.6-luna"


def test_codex_native_model_allowlist_preserves_compatibility() -> None:
    from autoskillit.core.types._type_backend import (
        CODEX_VALID_REASONING_EFFORTS,
        is_valid_codex_model_id,
    )

    assert is_valid_codex_model_id("gpt-5.6-sol")
    assert is_valid_codex_model_id("gpt-5.6-luna")
    assert is_valid_codex_model_id("gpt-5.5")
    assert not is_valid_codex_model_id("gpt-5.4")
    assert not is_valid_codex_model_id("gpt-5.4-mini")
    assert "max" in CODEX_VALID_REASONING_EFFORTS


def test_claude_alias_values_in_allowlist() -> None:
    from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES

    for key, value in CLAUDE_MODEL_ALIASES.items():
        assert value in VALID_CLAUDE_MODEL_IDS, (
            f"CLAUDE_MODEL_ALIASES[{key!r}] = {value!r} is not in VALID_CLAUDE_MODEL_IDS. "
            "Update VALID_CLAUDE_MODEL_IDS if the intended target model changed."
        )


def test_claude_sonnet_alias_uses_sonnet_5() -> None:
    from autoskillit.core.types._type_backend import CLAUDE_MODEL_ALIASES

    assert CLAUDE_MODEL_ALIASES["sonnet"] == "claude-sonnet-5"


def test_codex_alias_values_differ_from_keys() -> None:
    from autoskillit.core.types._type_backend import CODEX_MODEL_ALIASES

    for key, value in CODEX_MODEL_ALIASES.items():
        assert value != key, (
            f"CODEX_MODEL_ALIASES[{key!r}] == {value!r}: identity mapping passes raw Anthropic "
            "alias to Codex CLI. Codex requires real OpenAI model IDs."
        )
