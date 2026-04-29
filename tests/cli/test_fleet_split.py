from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


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
