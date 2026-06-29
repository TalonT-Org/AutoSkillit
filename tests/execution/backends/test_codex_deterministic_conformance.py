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
    CODEX_MCP_REQUIRED_KEYS,
    CODEX_TOOL_OUTPUT_TOKEN_LIMIT,
)
from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hook_registry import HOOK_REGISTRY_HASH, HOOKS_DIR

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "codex_ndjson"

_NON_UNKNOWN_EVENT_TYPES = [m for m in CodexEventType if m != CodexEventType.UNKNOWN]
_NON_UNKNOWN_ITEM_TYPES = [m for m in CodexItemType if m != CodexItemType.UNKNOWN]


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
            key: {
                "expected_type": "str"
                if key in ("command",)
                else "list"
                if key == "env_vars"
                else "float"
            }
            for key in sorted(CODEX_MCP_REQUIRED_KEYS)
        },
        "top_level_keys": {
            "tool_output_token_limit": {
                "expected_type": "int",
                "constraint": "minimum",
                "floor_value": CODEX_TOOL_OUTPUT_TOKEN_LIMIT,
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
        assert top["tool_output_token_limit"]["floor_value"] == CODEX_TOOL_OUTPUT_TOKEN_LIMIT, (
            f"CODEX_TOOL_OUTPUT_TOKEN_LIMIT drift: "
            f"fixture={top['tool_output_token_limit']['floor_value']} "
            f"vs live={CODEX_TOOL_OUTPUT_TOKEN_LIMIT}"
        )
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
            reloaded["top_level_keys"]["tool_output_token_limit"]["floor_value"]
            == CODEX_TOOL_OUTPUT_TOKEN_LIMIT
        )
