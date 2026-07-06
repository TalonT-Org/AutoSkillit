"""Tests: fleet run command gates — session guard, feature gates, CLAUDECODE relaxation."""

from __future__ import annotations

import json

import pytest

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
]


def _make_test_config(
    *, fleet: bool = False, fleet_headless_run: bool = False, experimental_enabled: bool = False
) -> object:
    """Build a lightweight config mock for `load_config` substitute."""
    return type(
        "C",
        (),
        {
            "features": {"fleet": fleet, "fleet_headless_run": fleet_headless_run},
            "experimental_enabled": experimental_enabled,
        },
    )()


class TestFleetRunGates:
    def test_fleet_run_blocks_in_leaf_session(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with FLEET_SESSION_TYPE_BLOCKED when SESSION_TYPE=leaf."""
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_SESSION_TYPE_BLOCKED"

    def test_fleet_run_blocks_in_skill_session(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with FLEET_SESSION_TYPE_BLOCKED when SESSION_TYPE=skill."""
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_SESSION_TYPE_BLOCKED"

    def test_fleet_run_allows_claudecode_env(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """CLAUDECODE env var is NOT a blocker — fleet_run proceeds past it to the feature gate."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(
                fleet=True, fleet_headless_run=False, experimental_enabled=True
            ),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        # Must NOT be a CLAUDECODE error — must be a feature gate error
        assert "CLAUDECODE" not in captured.out
        assert envelope["error"] == "FLEET_FEATURE_DISABLED"

    def test_fleet_run_exits_when_feature_disabled(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with JSON FLEET_FEATURE_DISABLED when fleet_headless_run is disabled."""
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        # Config: fleet=True, fleet_headless_run=False. experimental_enabled must be False too
        # so the feature is genuinely rejected (otherwise the blanket would promote it).
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(
                fleet=True, fleet_headless_run=False, experimental_enabled=False
            ),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_FEATURE_DISABLED"
        assert "fleet_headless_run" in envelope["user_visible_message"]

    def test_fleet_run_exits_when_fleet_disabled(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with JSON FLEET_FEATURE_DISABLED when base fleet feature is disabled."""
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        # Config: fleet=False, fleet_headless_run=False. experimental_enabled False.
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(
                fleet=False, fleet_headless_run=False, experimental_enabled=False
            ),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_FEATURE_DISABLED"
        assert "fleet" in envelope["user_visible_message"].lower()
