"""Verify _reap_stale_dispatches delegates to fleet._dispatch_reaper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]


def test_reap_delegates_to_dispatch_reaper(tmp_path: Path) -> None:
    from autoskillit.cli.fleet import _reap_stale_dispatches

    sp = tmp_path / "state.json"
    sp.write_text("{}")

    with patch("autoskillit.cli.fleet._fleet_lifecycle.reap_stale_dispatches") as mock_reap:
        _reap_stale_dispatches(sp, dry_run=True)

    mock_reap.assert_called_once_with(sp, dry_run=True)
