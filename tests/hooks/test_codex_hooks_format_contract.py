"""Contract tests: Codex hooks TOML format matches Codex serde expectations."""

from __future__ import annotations

import tomllib

import pytest

from autoskillit.execution import _serialize_toml
from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


class TestCodexTomlFormatContract:
    def test_codex_hooks_toml_uses_event_type_as_subtable_key(self):
        config = generate_codex_hooks_config()
        assert isinstance(config, dict), (
            f"generate_codex_hooks_config() must return dict[event_type, list[entry]], "
            f"got {type(config).__name__}"
        )
        for event_type, entries in config.items():
            assert event_type in ("PreToolUse", "PostToolUse", "SessionStart"), (
                f"Unknown event type key: {event_type}"
            )
            assert isinstance(entries, list)
            for entry in entries:
                assert "event" not in entry, (
                    "Entries must NOT have an 'event' scalar field — "
                    "the event type is the subtable key"
                )

    def test_serialized_toml_produces_hooks_dot_event_subtables(self):
        config = generate_codex_hooks_config()
        toml_text = _serialize_toml({"hooks": config})
        assert "[[hooks]]\n" not in toml_text, (
            "TOML must not use [[hooks]] — Codex expects [[hooks.PreToolUse]]"
        )
        for event_key in ("PreToolUse", "PostToolUse"):
            assert f"[[hooks.{event_key}]]" in toml_text, (
                f"TOML must contain [[hooks.{event_key}]] subtable"
            )
        parsed = tomllib.loads(toml_text)
        hooks = parsed.get("hooks", {})
        assert isinstance(hooks, dict), "Parsed hooks must be a dict keyed by event type"

    def test_no_event_scalar_in_toml_entries(self):
        config = generate_codex_hooks_config()
        toml_text = _serialize_toml({"hooks": config})
        parsed = tomllib.loads(toml_text)
        for event_type, entries in parsed.get("hooks", {}).items():
            for entry in entries:
                assert "event" not in entry, (
                    f"Entry under hooks.{event_type} has redundant 'event' scalar"
                )
