"""Shared alias registry consistency tests."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small, pytest.mark.model_contract]

# Test-only evidence for the global product floor; runtime checks use
# CLAUDE_CODE_CAPABILITIES.min_version rather than per-model thresholds.
# Lowest Claude Code release that serves each pinned id correctly: model-config requires
# 2.1.280 for Opus 5.5, 2.1.284 for Sonnet 5.5 and 2.1.293 for Haiku 5.5; 2.1.296 fixes
# Haiku 5.5 token counts behind gateways.
CLAUDE_MODEL_MIN_CLIENT_VERSIONS: dict[str, str] = {
    "claude-sonnet-5-5": "2.1.284",
    "claude-opus-5-5": "2.1.280",
    "claude-haiku-5-5": "2.1.296",
}


def test_anomaly_detection_aliases_keys_match_shared() -> None:
    from autoskillit.core.types.execution._type_backend import (
        CLAUDE_MODEL_ALIASES,
        CODEX_MODEL_ALIASES,
    )
    from autoskillit.execution.evidence.anomaly_detection import _MODEL_SHORT_ALIASES

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
    from autoskillit.core.types.execution._type_backend import (
        CODEX_MODEL_ALIASES,
        is_valid_codex_model_id,
    )

    for key, value in CODEX_MODEL_ALIASES.items():
        assert is_valid_codex_model_id(value), (
            f"CODEX_MODEL_ALIASES[{key!r}] = {value!r} is not a valid Codex model ID. "
            "Update CODEX_VALID_MODEL_IDS if the intended target model changed."
        )


def test_codex_aliases_map_classes_to_tiers() -> None:
    from autoskillit.core.types.execution._type_backend import (
        CODEX_EFFORT_MAPPING,
        CODEX_MODEL_ALIASES,
    )

    assert dict(CODEX_MODEL_ALIASES) == {
        "sonnet": "gpt-6-sol",
        "opus": "gpt-6-sol",
        "haiku": "gpt-6-luna",
    }
    assert CODEX_EFFORT_MAPPING == {"sonnet": "medium", "opus": "high", "haiku": "high"}


def test_codex_native_model_allowlist_preserves_compatibility() -> None:
    from autoskillit.core.types.execution._type_backend import (
        CODEX_VALID_REASONING_EFFORTS,
        is_valid_codex_model_id,
    )

    assert is_valid_codex_model_id("gpt-6-sol")
    assert is_valid_codex_model_id("gpt-6-luna")
    assert is_valid_codex_model_id("gpt-5.5")
    assert not is_valid_codex_model_id("gpt-5.6-sol")
    assert not is_valid_codex_model_id("gpt-5.6-luna")
    assert not is_valid_codex_model_id("gpt-5.6-terra")
    assert not is_valid_codex_model_id("gpt-5.4")
    assert not is_valid_codex_model_id("gpt-5.4-mini")
    assert "max" in CODEX_VALID_REASONING_EFFORTS


def test_claude_alias_values_in_allowlist() -> None:
    from autoskillit.core.types.execution._type_backend import CLAUDE_MODEL_ALIASES

    for key, value in CLAUDE_MODEL_ALIASES.items():
        assert value in CLAUDE_MODEL_MIN_CLIENT_VERSIONS, (
            f"CLAUDE_MODEL_ALIASES[{key!r}] = {value!r} is not in "
            "CLAUDE_MODEL_MIN_CLIENT_VERSIONS. Add the pinned id with its minimum release."
        )


def test_claude_aliases_map_classes_to_5_5_models() -> None:
    from autoskillit.core.types.execution._type_backend import CLAUDE_MODEL_ALIASES

    assert CLAUDE_MODEL_ALIASES == {
        "sonnet": "claude-sonnet-5-5",
        "opus": "claude-opus-5-5",
        "haiku": "claude-haiku-5-5",
    }


def test_claude_floor_supports_every_pinned_model() -> None:
    from packaging.version import Version

    from autoskillit.core.types.execution._type_backend import (
        CLAUDE_CODE_CAPABILITIES,
        CLAUDE_MODEL_ALIASES,
    )

    floor = CLAUDE_CODE_CAPABILITIES.min_version
    for model_class, model_id in CLAUDE_MODEL_ALIASES.items():
        required = CLAUDE_MODEL_MIN_CLIENT_VERSIONS[model_id]
        assert Version(floor) >= Version(required), (
            f"Claude class {model_class!r} pins {model_id!r}, which requires Claude Code "
            f"{required}; the configured floor is {floor}."
        )


def test_codex_alias_values_differ_from_keys() -> None:
    from autoskillit.core.types.execution._type_backend import CODEX_MODEL_ALIASES

    for key, value in CODEX_MODEL_ALIASES.items():
        assert value != key, (
            f"CODEX_MODEL_ALIASES[{key!r}] == {value!r}: identity mapping passes raw Anthropic "
            "alias to Codex CLI. Codex requires real OpenAI model IDs."
        )
