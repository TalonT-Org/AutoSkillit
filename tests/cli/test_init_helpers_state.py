"""Tests for parse-failure write guard in _log_secret_scan_bypass."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _write_state(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestSecretScanBypassParseFailure:
    def test_log_secret_scan_bypass_refuses_overwrite_on_corrupt_yaml(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _log_secret_scan_bypass

        state_path = tmp_path / ".autoskillit" / ".state.yaml"
        corrupt_content = "{{{invalid"
        _write_state(state_path, corrupt_content)

        with pytest.raises(SystemExit):
            _log_secret_scan_bypass(tmp_path)

        assert state_path.read_text() == corrupt_content

    def test_log_secret_scan_bypass_works_on_missing_file(self, tmp_path: Path) -> None:
        from autoskillit.cli._init_helpers import _log_secret_scan_bypass

        state_path = tmp_path / ".autoskillit" / ".state.yaml"
        assert not state_path.exists()

        _log_secret_scan_bypass(tmp_path)

        assert state_path.exists()
        content = state_path.read_text()
        assert "secret_scan_bypass_accepted" in content
