"""Tests for cli/_mcp_names.py — MCP prefix detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli._mcp_names import (
    DIRECT_PREFIX,
    MARKETPLACE_PREFIX,
    detect_autoskillit_mcp_prefix,
)

pytestmark = [pytest.mark.layer("cli")]

_PLUGIN_KEY = "autoskillit@autoskillit-local"


class TestDetectAutoskillitMcpPrefix:
    def test_returns_marketplace_prefix_when_plugin_key_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "installed_plugins.json"
        f.write_text(json.dumps({"version": 2, "plugins": {_PLUGIN_KEY: []}}))
        monkeypatch.setattr("autoskillit.cli._mcp_names._installed_plugins_path", lambda: f)
        assert detect_autoskillit_mcp_prefix() == MARKETPLACE_PREFIX

    def test_returns_direct_prefix_when_file_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.cli._mcp_names._installed_plugins_path",
            lambda: tmp_path / "no_such_file.json",
        )
        assert detect_autoskillit_mcp_prefix() == DIRECT_PREFIX

    def test_returns_direct_prefix_when_autoskillit_key_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "installed_plugins.json"
        f.write_text(json.dumps({"version": 2, "plugins": {"other@other-local": []}}))
        monkeypatch.setattr("autoskillit.cli._mcp_names._installed_plugins_path", lambda: f)
        assert detect_autoskillit_mcp_prefix() == DIRECT_PREFIX

    def test_returns_direct_prefix_on_malformed_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "installed_plugins.json"
        f.write_text("not valid json {{{")
        monkeypatch.setattr("autoskillit.cli._mcp_names._installed_plugins_path", lambda: f)
        assert detect_autoskillit_mcp_prefix() == DIRECT_PREFIX

    def test_direct_prefix_ends_with_double_underscore(self) -> None:
        assert DIRECT_PREFIX.endswith("__")

    def test_marketplace_prefix_ends_with_double_underscore(self) -> None:
        assert MARKETPLACE_PREFIX.endswith("__")
