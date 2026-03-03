"""Tests for serve() two-phase logging initialization."""

from __future__ import annotations

import logging as _stdlib_logging
from unittest.mock import patch

import structlog.testing

import autoskillit.server as server_mod


class TestServeLoggingPhases:
    """Verify serve() calls configure_logging twice: early init then config-driven."""

    def test_serve_reconfigures_logging_from_config(self, tmp_path, monkeypatch):
        """serve() calls configure_logging twice: early init at INFO, then
        reconfigures after config load with the config-specified level."""
        import autoskillit.cli as cli_mod

        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("logging:\n  level: DEBUG\n")

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve()

        # Should have been called at least twice
        assert mock_configure.call_count >= 2, (
            f"Expected >= 2 calls, got {mock_configure.call_count}"
        )

        # Phase 2: called with DEBUG (from config, min(INFO, DEBUG) = DEBUG)
        second_call = mock_configure.call_args_list[1]
        level_arg = second_call.kwargs.get(
            "level", second_call.args[0] if second_call.args else None
        )
        assert level_arg == _stdlib_logging.DEBUG

    def test_serve_no_reconfig_when_defaults(self, tmp_path, monkeypatch):
        """serve() does NOT call configure_logging a second time when config
        uses default level=INFO and json_output=None."""
        import autoskillit.cli as cli_mod

        monkeypatch.chdir(tmp_path)
        # No config file — defaults only

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve()

        # Only phase 1 — no reconfig needed when defaults match
        assert mock_configure.call_count == 1

    def test_verbose_flag_wins_over_config(self, tmp_path, monkeypatch):
        """--verbose flag ensures DEBUG even if config says INFO."""
        import autoskillit.cli as cli_mod

        monkeypatch.chdir(tmp_path)

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve(verbose=True)

        # Phase 1 should be DEBUG from --verbose
        first_call = mock_configure.call_args_list[0]
        level_arg = first_call.kwargs.get("level", first_call.args[0] if first_call.args else None)
        assert level_arg == _stdlib_logging.DEBUG

    def test_json_output_from_config(self, tmp_path, monkeypatch):
        """serve() reconfigures when config sets json_output explicitly."""
        import autoskillit.cli as cli_mod

        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("logging:\n  json_output: true\n")

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve()

        # Should have been called twice (json_output is not None triggers reconfig)
        assert mock_configure.call_count >= 2
        second_call = mock_configure.call_args_list[1]
        assert second_call.kwargs.get("json_output") is True
