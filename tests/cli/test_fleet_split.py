from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_CLI_SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"


def test_fleet_display_file_exists():
    assert (_CLI_SRC / "_fleet_display.py").exists()


def test_fleet_lifecycle_file_exists():
    assert (_CLI_SRC / "_fleet_lifecycle.py").exists()


def test_fleet_session_file_exists():
    assert (_CLI_SRC / "_fleet_session.py").exists()


def test_fleet_display_importable():
    from autoskillit.cli._fleet_display import _build_status_rows, _humanize  # noqa: F401


def test_fleet_lifecycle_importable():
    from autoskillit.cli._fleet_lifecycle import (  # noqa: F401
        _fleet_signal_guard,
        _reap_stale_dispatches,
    )


def test_fleet_session_importable():
    from autoskillit.cli._fleet_session import _launch_fleet_session  # noqa: F401


def test_fleet_facade_exports_fleet_app():
    from autoskillit.cli._fleet import fleet_app, render_fleet_error  # noqa: F401
