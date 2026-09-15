"""Tests for Codex runtime policy loading and resolution."""

from __future__ import annotations

import dataclasses

import pytest
import yaml

from autoskillit.config import AutomationConfig, CodexRuntimeConfig, load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


def test_default_runtime_policy_resolves_to_immutable_deny_spec() -> None:
    spec = AutomationConfig().codex_runtime.resolve()

    assert spec.auto_compaction_policy == "deny"
    assert spec.context_window_tokens is None
    assert spec.auto_compact_threshold_tokens is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.context_window_tokens = 1  # type: ignore[misc]


def test_runtime_policy_loads_optional_tuning_from_yaml(tmp_path) -> None:
    config_dir = tmp_path / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "codex_runtime": {
                    "context_window_tokens": 200_000,
                    "auto_compact_threshold_tokens": 180_000,
                }
            }
        )
    )

    spec = load_config(tmp_path).codex_runtime.resolve()

    assert spec.context_window_tokens == 200_000
    assert spec.auto_compact_threshold_tokens == 180_000


def test_runtime_policy_loads_optional_tuning_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTOSKILLIT_CODEX_RUNTIME__CONTEXT_WINDOW_TOKENS", "128000")
    monkeypatch.setenv("AUTOSKILLIT_CODEX_RUNTIME__AUTO_COMPACT_THRESHOLD_TOKENS", "115000")

    spec = load_config(tmp_path).codex_runtime.resolve()

    assert spec.context_window_tokens == 128_000
    assert spec.auto_compact_threshold_tokens == 115_000


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    [
        ({"auto_compaction_policy": "allow"}, "auto_compaction_policy"),
        ({"context_window_tokens": True}, "context_window_tokens"),
        ({"context_window_tokens": 0}, "context_window_tokens"),
        ({"auto_compact_threshold_tokens": -1}, "auto_compact_threshold_tokens"),
    ],
)
def test_runtime_policy_rejects_invalid_values(kwargs, field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        CodexRuntimeConfig(**kwargs)
