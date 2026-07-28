"""Tests for server version info, plugin metadata, lazy init, and telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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

    @pytest.mark.parametrize(
        ("manifest_version", "expected_plugin_version"),
        [
            pytest.param("0.0.0", "0.0.0", id="mismatch"),
            pytest.param(None, None, id="missing-manifest"),
        ],
    )
    def test_version_info_reports_package_root_negative_states(
        self,
        tmp_path,
        monkeypatch,
        manifest_version: str | None,
        expected_plugin_version: str | None,
    ):
        import autoskillit.version as version_module
        from autoskillit.server import version_info

        package_root = tmp_path / "autoskillit"
        package_root.mkdir()
        if manifest_version is not None:
            manifest = package_root / ".claude-plugin" / "plugin.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"name": "autoskillit", "version": manifest_version}))

        monkeypatch.setattr(version_module.ir, "files", lambda _package: package_root)
        monkeypatch.setattr(
            version_module.importlib.metadata,
            "version",
            lambda _package: "1.2.3",
        )
        version_module.version_info.cache_clear()
        try:
            info = version_info()
        finally:
            version_module.version_info.cache_clear()

        assert info == {
            "package_version": "1.2.3",
            "plugin_json_version": expected_plugin_version,
            "match": False,
        }

    def test_version_info_does_not_acquire_runtime_plugin_artifacts(self, tool_ctx):
        from autoskillit.server import version_info

        class ExplodingAuthority:
            def acquire_launch_binding(self, *, backend, load_mode):
                raise AssertionError("version_info must inspect the installed package")

        tool_ctx.plugin_authority = ExplodingAuthority()
        info = version_info()
        assert info["match"] is True
        assert info["package_version"] == info["plugin_json_version"]

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

        from autoskillit.core.types._type_results import ProviderOutcome
        from autoskillit.core.types._type_results_execution import (
            RecipeIdentity,
            SessionTelemetry,
        )
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
            telemetry=SessionTelemetry(
                token_usage={
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_write_tokens": 0,
                    "cache_read_tokens": 0,
                },
                timing_seconds=10.0,
                audit_record=None,
                github_api_usage=None,
                github_api_requests=0,
                loc_insertions=0,
                loc_deletions=0,
            ),
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=RecipeIdentity.empty(),
        )

        # Write a clear marker 3 hours ago (after the session completed)
        three_hours_ago = datetime.now(UTC) - timedelta(hours=3)
        (log_dir / ".telemetry_cleared_at").write_text(three_hours_ago.isoformat())

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        _state._initialize(tool_ctx)

        # The old-session happened before the clear marker → should NOT be replayed
        report = tool_ctx.token_log.get_report()
        assert all(s["step_name"] != "old-step" for s in report)
