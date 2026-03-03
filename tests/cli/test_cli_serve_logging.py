"""Tests for serve() two-phase logging initialization."""

from __future__ import annotations

from unittest.mock import call, patch

import structlog.testing


class TestServeLoggingPhases:
    """Verify serve() calls configure_logging twice: early init then config-driven."""

    def test_serve_reconfigures_logging_from_config(self, tmp_path, monkeypatch):
        """serve() calls configure_logging twice: early init at INFO, then
        reconfigures after config load with the config-specified level."""
        import logging as _stdlib_logging

        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("logging:\n  level: DEBUG\n")

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.cli.app.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve()

        # Should have been called at least twice
        assert mock_configure.call_count >= 2

        # Phase 1: called with INFO
        first_call = mock_configure.call_args_list[0]
        assert first_call == call(
            level=_stdlib_logging.INFO,
            json_output=True,  # not a TTY in test
            stream=mock_configure.call_args_list[0].kwargs.get(
                "stream", first_call[1].get("stream")
            ),
        )

        # Phase 2: called with DEBUG (from config)
        second_call = mock_configure.call_args_list[1]
        assert second_call.kwargs.get("level") == _stdlib_logging.DEBUG or (
            len(second_call.args) > 0 and second_call.args[0] == _stdlib_logging.DEBUG
        )

    def test_serve_no_reconfig_when_defaults(self, tmp_path, monkeypatch):
        """serve() does NOT call configure_logging a second time when config
        uses default level=INFO and json_output=None."""
        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)
        # No config file — defaults only

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.cli.app.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve()

        # Only phase 1 — no reconfig needed when defaults match
        assert mock_configure.call_count == 1

    def test_verbose_flag_wins_over_config(self, tmp_path, monkeypatch):
        """--verbose flag ensures DEBUG even if config says INFO."""
        import logging as _stdlib_logging

        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)
        # Config says INFO (default), --verbose says DEBUG
        # min(INFO, DEBUG) = DEBUG

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.cli.app.configure_logging") as mock_configure,
            structlog.testing.capture_logs(),
        ):
            cli_mod.serve(verbose=True)

        # Phase 1 should be DEBUG from --verbose
        first_call = mock_configure.call_args_list[0]
        assert first_call.kwargs.get("level") == _stdlib_logging.DEBUG or (
            len(first_call.args) > 0 and first_call.args[0] == _stdlib_logging.DEBUG
        )
