"""Merge-blocking CI tests for Codex deterministic conformance.

Seals the ``CodexEventType`` / ``CodexItemType`` vocabulary, hook event
formats, and ``config.toml`` schema template against undetected drift.
All tests are pure Python assertions against committed JSON fixtures —
no live CLI, no subprocess, no network I/O.

Run ``pytest --update-fixtures -n0`` to regenerate fixtures in-place
after a deliberate vocabulary or format change.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.core.types._type_enums import CodexEventType, CodexItemType
from autoskillit.execution.backends._codex_config import (
    CODEX_AUTO_COMPACT_LIMIT,
    CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
    CODEX_MCP_REQUIRED_KEYS,
)
from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hook_registry import HOOK_REGISTRY_HASH, HOOKS_DIR
from tests.execution.backends._conformance_assertions import (
    assert_boundary_spill_behavior,
    assert_config_schema,
    assert_hook_event_format,
    assert_inline_within_byte_budget,
    assert_no_unknown_event_types,
    assert_order_up_marker_standalone,
    assert_sentinels_present,
    assert_session_start_present,
    assert_spill_artifact_integrity,
    assert_terminal_sentinel_preserved,
    assert_turn_completed_usage_nonzero,
    assert_vocabulary_coverage,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "codex_ndjson"

_NON_UNKNOWN_EVENT_TYPES = [m for m in CodexEventType if m != CodexEventType.UNKNOWN]
_NON_UNKNOWN_ITEM_TYPES = [m for m in CodexItemType if m != CodexItemType.UNKNOWN]

_MCP_KEY_EXPECTED_TYPES: dict[str, str] = {
    "command": "str",
    "env_vars": "list",
    "startup_timeout_sec": "float",
    "tool_timeout_sec": "float",
}


def _sanitize_hooks(hooks: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Replace machine-specific paths and hashes with deterministic placeholders."""
    raw = json.dumps(hooks, sort_keys=True)
    raw = raw.replace(str(HOOKS_DIR), "SANITIZED_HOOKS_DIR")
    raw = re.sub(
        r'"trusted_hash":\s*"[a-f0-9]{64}"', '"trusted_hash": "SANITIZED_FOR_DETERMINISM"', raw
    )
    return json.loads(raw)


def _assert_single_worker(request: pytest.FixtureRequest) -> None:
    """Skip if running under xdist parallel workers."""
    if hasattr(request.config, "workerinput"):
        pytest.skip(
            "--update-fixtures must be run with -n0 (single worker) "
            "to prevent concurrent writes under xdist -n 4"
        )


def _generate_config_template() -> dict:
    """Build the config.toml schema template from live constants."""
    return {
        "_codex_mcp_required_keys": sorted(CODEX_MCP_REQUIRED_KEYS),
        "mcp_server_entry_required_keys": {
            key: {"expected_type": _MCP_KEY_EXPECTED_TYPES[key]}
            for key in sorted(CODEX_MCP_REQUIRED_KEYS)
        },
        "top_level_keys": {
            "tool_output_token_limit": {
                "expected_type": "int",
                "constraint": "exact",
                "expected_value": CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
            },
            "model_auto_compact_token_limit": {
                "expected_type": "int",
                "constraint": "minimum",
                "floor_value": CODEX_AUTO_COMPACT_LIMIT,
            },
        },
    }


class TestCodexEventTypeVocabulary:
    @pytest.mark.parametrize(
        "member",
        _NON_UNKNOWN_EVENT_TYPES,
        ids=[m.value for m in _NON_UNKNOWN_EVENT_TYPES],
    )
    def test_event_schema_exists_and_valid(self, member: CodexEventType) -> None:
        fixture_path = FIXTURES_DIR / f"event_{member.value}.json"
        assert fixture_path.exists(), (
            f"Missing fixture for CodexEventType.{member.name} — "
            f"expected {fixture_path.name}. Add the schema file or run with --update-fixtures."
        )
        schema = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert isinstance(schema.get("required"), list) and len(schema["required"]) > 0, (
            f"{fixture_path.name}: 'required' must be a non-empty list"
        )
        assert schema.get("additionalProperties") is False, (
            f"{fixture_path.name}: 'additionalProperties' must be false (sealed schema)"
        )

    def test_sealed_enumeration(self) -> None:
        missing = []
        for member in _NON_UNKNOWN_EVENT_TYPES:
            fixture_path = FIXTURES_DIR / f"event_{member.value}.json"
            if not fixture_path.exists():
                missing.append(member.value)
        assert not missing, (
            f"Sealed enumeration violated — CodexEventType members without fixture files: "
            f"{missing}. Add schema fixtures or remove the enum members."
        )


class TestCodexItemTypeVocabulary:
    @pytest.mark.parametrize(
        "member",
        _NON_UNKNOWN_ITEM_TYPES,
        ids=[m.value for m in _NON_UNKNOWN_ITEM_TYPES],
    )
    def test_item_schema_exists_and_valid(self, member: CodexItemType) -> None:
        fixture_path = FIXTURES_DIR / f"item_{member.value}.json"
        assert fixture_path.exists(), (
            f"Missing fixture for CodexItemType.{member.name} — "
            f"expected {fixture_path.name}. Add the schema file or run with --update-fixtures."
        )
        schema = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert isinstance(schema.get("required"), list) and len(schema["required"]) > 0, (
            f"{fixture_path.name}: 'required' must be a non-empty list"
        )
        assert schema.get("additionalProperties") is False, (
            f"{fixture_path.name}: 'additionalProperties' must be false (sealed schema)"
        )

    def test_sealed_enumeration(self) -> None:
        missing = []
        for member in _NON_UNKNOWN_ITEM_TYPES:
            fixture_path = FIXTURES_DIR / f"item_{member.value}.json"
            if not fixture_path.exists():
                missing.append(member.value)
        assert not missing, (
            f"Sealed enumeration violated — CodexItemType members without fixture files: "
            f"{missing}. Add schema fixtures or remove the enum members."
        )


class TestCodexHookEventFormatFixture:
    _SNAPSHOT_PATH = FIXTURES_DIR / "hook_event_format_snapshot.json"

    def test_hook_snapshot_matches_live_registry(self) -> None:
        snapshot = json.loads(self._SNAPSHOT_PATH.read_text(encoding="utf-8"))
        live_hooks = generate_codex_hooks_config()
        sanitized_live = _sanitize_hooks(live_hooks)
        assert_hook_event_format({"hooks": live_hooks})

        assert snapshot["_registry_hash"] == HOOK_REGISTRY_HASH, (
            f"HOOK_REGISTRY_HASH drift detected: "
            f"fixture={snapshot['_registry_hash'][:12]}… vs live={HOOK_REGISTRY_HASH[:12]}…. "
            f"Rerun with: pytest --update-fixtures -n0 "
            f"tests/execution/backends/test_codex_deterministic_conformance.py"
        )
        assert sanitized_live == snapshot["hooks"], (
            "Hook event format structure drift detected — "
            "live generate_codex_hooks_config() output (sanitized) differs from snapshot. "
            "Rerun with: pytest --update-fixtures -n0 "
            "tests/execution/backends/test_codex_deterministic_conformance.py"
        )

    def test_update_fixtures_writes_snapshot(
        self, update_fixtures: bool, request: pytest.FixtureRequest
    ) -> None:
        if not update_fixtures:
            pytest.skip("--update-fixtures not set")
        _assert_single_worker(request)

        live_hooks = generate_codex_hooks_config()
        sanitized_live = _sanitize_hooks(live_hooks)
        snapshot_data = {
            "_registry_hash": HOOK_REGISTRY_HASH,
            "hooks": sanitized_live,
        }
        self._SNAPSHOT_PATH.write_text(
            json.dumps(snapshot_data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        reloaded = json.loads(self._SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert reloaded["_registry_hash"] == HOOK_REGISTRY_HASH
        assert reloaded["hooks"] == sanitized_live


class TestCodexConfigTomlSchemaTemplate:
    _TEMPLATE_PATH = FIXTURES_DIR / "config_toml_schema_template.json"

    def test_generator_preserves_distinct_top_level_constraint_semantics(self) -> None:
        top = _generate_config_template()["top_level_keys"]
        assert top["tool_output_token_limit"] == {
            "constraint": "exact",
            "expected_type": "int",
            "expected_value": CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
        }
        assert top["model_auto_compact_token_limit"] == {
            "constraint": "minimum",
            "expected_type": "int",
            "floor_value": CODEX_AUTO_COMPACT_LIMIT,
        }

    def test_required_keys_present(self) -> None:
        template = json.loads(self._TEMPLATE_PATH.read_text(encoding="utf-8"))
        fixture_keys = set(template["_codex_mcp_required_keys"])
        assert CODEX_MCP_REQUIRED_KEYS == fixture_keys, (
            f"CODEX_MCP_REQUIRED_KEYS drift: live={sorted(CODEX_MCP_REQUIRED_KEYS)}, "
            f"fixture={sorted(fixture_keys)}. "
            f"Rerun with: pytest --update-fixtures -n0 "
            f"tests/execution/backends/test_codex_deterministic_conformance.py"
        )

    def test_pinned_constants_match(self) -> None:
        template = json.loads(self._TEMPLATE_PATH.read_text(encoding="utf-8"))
        top = template["top_level_keys"]
        assert top["tool_output_token_limit"] == {
            "constraint": "exact",
            "expected_type": "int",
            "expected_value": CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
        }
        assert top["model_auto_compact_token_limit"]["floor_value"] == CODEX_AUTO_COMPACT_LIMIT, (
            f"CODEX_AUTO_COMPACT_LIMIT drift: "
            f"fixture={top['model_auto_compact_token_limit']['floor_value']} "
            f"vs live={CODEX_AUTO_COMPACT_LIMIT}"
        )
        mcp_keys = template["mcp_server_entry_required_keys"]
        assert "startup_timeout_sec" in mcp_keys, (
            "startup_timeout_sec missing from MCP entry template"
        )
        assert "tool_timeout_sec" in mcp_keys, "tool_timeout_sec missing from MCP entry template"

    def test_update_fixtures_regenerates_template(
        self, update_fixtures: bool, request: pytest.FixtureRequest
    ) -> None:
        if not update_fixtures:
            pytest.skip("--update-fixtures not set")
        _assert_single_worker(request)

        template = _generate_config_template()
        self._TEMPLATE_PATH.write_text(
            json.dumps(template, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        reloaded = json.loads(self._TEMPLATE_PATH.read_text(encoding="utf-8"))
        assert set(reloaded["_codex_mcp_required_keys"]) == CODEX_MCP_REQUIRED_KEYS
        assert (
            reloaded["top_level_keys"]["tool_output_token_limit"]["expected_value"]
            == CODEX_HISTORY_RETENTION_TOKEN_LIMIT
        )
        assert (
            reloaded["top_level_keys"]["model_auto_compact_token_limit"]["floor_value"]
            == CODEX_AUTO_COMPACT_LIMIT
        )


class TestConformanceAssertionsSyntheticExercise:
    """Exercise event-oriented conformance assertions with synthetic data."""

    _EVENTS: list[dict] = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "item.started"},
        {"type": "item.completed", "item": {"content": [{"text": "%%MARKER%%"}]}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]

    def test_vocabulary_coverage(self) -> None:
        assert_vocabulary_coverage(self._EVENTS, {"thread.started", "turn.completed"})

    def test_no_unknown_event_types(self) -> None:
        assert_no_unknown_event_types(self._EVENTS)

    def test_session_start_present(self) -> None:
        assert_session_start_present(self._EVENTS)

    def test_turn_completed_usage_nonzero(self) -> None:
        assert_turn_completed_usage_nonzero(self._EVENTS)

    def test_order_up_marker_standalone(self) -> None:
        assert_order_up_marker_standalone(self._EVENTS, "%%MARKER%%")

    def test_config_schema_valid(self) -> None:
        assert_config_schema({"model": "o4-mini", "instructions": "test"}, "synthetic")

    def test_output_budget_assertions(self, tmp_path: Path) -> None:
        assert_boundary_spill_behavior({4999: False, 5000: False, 5001: True}, 5000)
        sentinels = ("HEAD", "MIDDLE", "TAIL")
        payload = "HEAD\nMIDDLE\nTAIL"
        assert_sentinels_present(payload, sentinels)
        artifact = tmp_path / "spill.txt"
        artifact.write_text(payload, encoding="utf-8")
        assert_spill_artifact_integrity(str(artifact), payload, sentinels)
        assert_inline_within_byte_budget("bounded", 7, envelope_slack_bytes=0)
        assert_terminal_sentinel_preserved(
            "complete\nTERMINAL",
            "TERMINAL",
            ("[truncated]", "... output omitted ..."),
        )


class TestConformanceAssertionsFullCoverage:
    """Meta-test: every assert_* name from _conformance_assertions is called in this file."""

    def test_all_assertions_called(self) -> None:
        import ast
        import importlib
        import inspect

        import tests.execution.backends._conformance_assertions as ca_mod

        exported_names = {
            name
            for name in dir(ca_mod)
            if name.startswith("assert_") and callable(getattr(ca_mod, name))
        }

        test_modules = [
            "tests.execution.backends.test_codex_deterministic_conformance",
            "tests.execution.backends.test_cli_conformance_probes",
        ]
        called_names: set[str] = set()
        for mod_name in test_modules:
            source = inspect.getsource(importlib.import_module(mod_name))
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id in exported_names:
                        called_names.add(func.id)
                    elif isinstance(func, ast.Attribute) and func.attr in exported_names:
                        called_names.add(func.attr)

        missing = exported_names - called_names
        assert not missing, f"Conformance assertions not called in test files: {sorted(missing)}"
