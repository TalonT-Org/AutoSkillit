"""Tests for server version info, plugin metadata, lazy init, and telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("server")]


class TestPluginMetadataExists:
    """T1: Plugin metadata files are shipped in the package."""

    def test_plugin_json_exists(self):
        """Package contains .claude-plugin/plugin.json with correct fields."""
        import autoskillit

        pkg = Path(autoskillit.__file__).parent
        manifest = pkg / ".claude-plugin" / "plugin.json"
        assert manifest.is_file()
        data = json.loads(manifest.read_text())
        assert data["name"] == "autoskillit"
        assert data["version"] == autoskillit.__version__

    def test_mcp_json_exists(self):
        """Package contains .mcp.json with autoskillit server entry."""
        import autoskillit

        pkg = Path(autoskillit.__file__).parent
        mcp_cfg = pkg / ".mcp.json"
        assert mcp_cfg.is_file()
        data = json.loads(mcp_cfg.read_text())
        assert "autoskillit" in data["mcpServers"]
        assert data["mcpServers"]["autoskillit"]["command"] == "autoskillit"


class TestPluginDirConstant:
    """T6: tool_ctx.plugin_dir defaults to the package root directory."""

    def test_plugin_dir_assignment_is_visible_via_get_ctx(self, tool_ctx):
        """By default tool_ctx.plugin_dir is set to tmp_path by the fixture.

        The real package dir is what the server uses at runtime (set by cli.py serve()).
        This test verifies that the fixture wires plugin_dir through _ctx correctly.
        """
        import autoskillit

        # The real package dir is what the server sets at startup.
        # We verify the attribute path works (tool_ctx.plugin_dir is accessible).
        real_pkg_dir = str(Path(autoskillit.__file__).parent)
        # tool_ctx uses tmp_path; set it to verify end-to-end wiring
        tool_ctx.plugin_dir = real_pkg_dir
        from autoskillit.server import _get_ctx

        assert _get_ctx().plugin_dir == real_pkg_dir


class TestVersionInfo:
    """version_info() returns package and plugin.json versions."""

    def test_version_info_returns_package_and_plugin_versions(self, monkeypatch):
        from autoskillit import __version__
        from autoskillit.server import _state, version_info

        monkeypatch.setattr(_state, "_ctx", None)
        info = version_info()
        assert isinstance(info["package_version"], str)
        assert isinstance(info["plugin_json_version"], str)
        assert info["package_version"] == __version__
        assert info["match"] is True

    def test_version_info_detects_mismatch(self, tmp_path, tool_ctx):
        from autoskillit.server import version_info

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path)
        info = version_info()
        assert info["match"] is False
        assert info["package_version"] != info["plugin_json_version"]
        assert info["plugin_json_version"] == "0.0.0"

    def test_version_info_handles_missing_plugin_json(self, tmp_path, tool_ctx):
        from autoskillit.server import version_info

        tool_ctx.plugin_dir = str(tmp_path)
        info = version_info()
        assert info["plugin_json_version"] is None
        assert info["match"] is False

    def test_version_info_is_public(self, monkeypatch):
        """version_info must be a public function — no underscore prefix."""
        from autoskillit import server
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", None)
        assert hasattr(server, "version_info"), "server.version_info must exist"
        assert not hasattr(server, "_version_info"), "server._version_info must be removed"
        result = server.version_info()
        assert set(result.keys()) >= {"package_version", "plugin_json_version", "match"}


class TestServerLazyInit:
    """Tests for the _ctx / _initialize() / _get_ctx() / _get_config() pattern."""

    def test_server_import_does_not_call_load_config(self, monkeypatch):
        """Importing server.py must not trigger load_config() as a side effect."""
        import sys

        import autoskillit

        # Restore both the package attribute and sys.modules entry after the test so
        # later tests in the same xdist worker see the original module object.
        monkeypatch.setattr(autoskillit, "server", autoskillit.server)
        monkeypatch.delitem(sys.modules, "autoskillit.server", raising=False)

        with patch("autoskillit.config.load_config") as mock_load:
            import autoskillit.server  # noqa: F401
        assert not mock_load.called

    def test_get_ctx_raises_before_initialize(self, monkeypatch):
        """_get_ctx() raises RuntimeError when _ctx is None."""
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", None)
        with pytest.raises(RuntimeError, match="serve\\(\\) must be called"):
            _state._get_ctx()

    def test_get_config_raises_before_initialize(self, monkeypatch):
        """_get_config() raises RuntimeError when _ctx is None."""
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", None)
        with pytest.raises(RuntimeError, match="serve\\(\\) must be called"):
            _state._get_config()


class TestInitializeClearMarker:
    """_initialize respects telemetry_cleared_at fence for drift prevention."""

    def test_initialize_uses_clear_marker_as_since_bound(self, tool_ctx, tmp_path, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from autoskillit.execution.session_log import (
            flush_session_log,
        )
        from autoskillit.server import _state

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Write a session that completed 5 hours ago (within 24h window)
        five_hours_ago = datetime.now(UTC) - timedelta(hours=5)
        flush_session_log(
            log_dir=str(log_dir),
            cwd="/tmp",
            session_id="old-session",
            pid=999,
            skill_command="/autoskillit:foo",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts=five_hours_ago.isoformat(),
            proc_snapshots=None,
            step_name="old-step",
            token_usage={
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            timing_seconds=10.0,
        )

        # Write a clear marker 3 hours ago (after the session completed)
        three_hours_ago = datetime.now(UTC) - timedelta(hours=3)
        (log_dir / ".telemetry_cleared_at").write_text(three_hours_ago.isoformat())

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        _state._initialize(tool_ctx)

        # The old-session happened before the clear marker → should NOT be replayed
        report = tool_ctx.token_log.get_report()
        assert all(s["step_name"] != "old-step" for s in report)
