"""Tests for cli/_mcp_names.py — MCP prefix detection."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.cli._mcp_names import (
    DIRECT_PREFIX,
    MARKETPLACE_PREFIX,
    detect_autoskillit_mcp_prefix,
)
from autoskillit.core import CLAUDE_CODE_CAPABILITIES

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_PLUGIN_KEY = "autoskillit@autoskillit-local"


class TestDetectAutoskillitMcpPrefix:
    @pytest.fixture(autouse=True)
    def _clear_prefix_cache(self):  # noqa: ANN204
        from autoskillit.core._plugin_ids import detect_autoskillit_mcp_prefix as _fn

        _fn.cache_clear()
        yield
        _fn.cache_clear()

    def test_returns_marketplace_prefix_when_plugin_key_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "installed_plugins.json"
        f.write_text(json.dumps({"version": 2, "plugins": {_PLUGIN_KEY: []}}))
        monkeypatch.setattr("autoskillit.core._plugin_ids._installed_plugins_path", lambda: f)
        assert detect_autoskillit_mcp_prefix(CLAUDE_CODE_CAPABILITIES) == MARKETPLACE_PREFIX

    def test_returns_direct_prefix_when_file_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.core._plugin_ids._installed_plugins_path",
            lambda: tmp_path / "no_such_file.json",
        )
        assert detect_autoskillit_mcp_prefix(CLAUDE_CODE_CAPABILITIES) == DIRECT_PREFIX

    def test_returns_direct_prefix_when_autoskillit_key_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "installed_plugins.json"
        f.write_text(json.dumps({"version": 2, "plugins": {"other@other-local": []}}))
        monkeypatch.setattr("autoskillit.core._plugin_ids._installed_plugins_path", lambda: f)
        assert detect_autoskillit_mcp_prefix(CLAUDE_CODE_CAPABILITIES) == DIRECT_PREFIX

    def test_returns_direct_prefix_on_malformed_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "installed_plugins.json"
        f.write_text("not valid json {{{")
        monkeypatch.setattr("autoskillit.core._plugin_ids._installed_plugins_path", lambda: f)
        assert detect_autoskillit_mcp_prefix(CLAUDE_CODE_CAPABILITIES) == DIRECT_PREFIX

    def test_non_marketplace_backend_does_not_read_claude_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capabilities = replace(
            CLAUDE_CODE_CAPABILITIES,
            claude_marketplace_tool_prefix_capable=False,
        )

        def fail_if_read() -> Path:
            raise AssertionError("Claude marketplace state must not be read")

        monkeypatch.setattr(
            "autoskillit.core._plugin_ids._installed_plugins_path",
            fail_if_read,
        )
        assert detect_autoskillit_mcp_prefix(capabilities) == DIRECT_PREFIX

    def test_direct_prefix_ends_with_double_underscore(self) -> None:
        assert DIRECT_PREFIX.endswith("__")

    def test_marketplace_prefix_ends_with_double_underscore(self) -> None:
        assert MARKETPLACE_PREFIX.endswith("__")
