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
        assert not (state_path.parent / ".gitignore").exists()

    def test_log_secret_scan_bypass_works_on_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli import _init_helpers

        state_path = tmp_path / ".autoskillit" / ".state.yaml"
        assert not state_path.exists()

        write_state = _init_helpers.atomic_write

        def write_after_ignore(path: Path, content: str) -> None:
            assert ".state.yaml" in (state_path.parent / ".gitignore").read_text().splitlines()
            write_state(path, content)

        monkeypatch.setattr(_init_helpers, "atomic_write", write_after_ignore)
        _init_helpers._log_secret_scan_bypass(tmp_path)

        assert state_path.exists()
        content = state_path.read_text()
        assert "secret_scan_bypass_accepted" in content
        ignore_lines = (state_path.parent / ".gitignore").read_text().splitlines()
        assert ".state.yaml" in ignore_lines

    def test_log_secret_scan_bypass_preserves_unknown_state_and_config(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _log_secret_scan_bypass
        from autoskillit.core import load_yaml

        state_path = tmp_path / ".autoskillit" / ".state.yaml"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("custom: keep\nsafety:\n  other: keep\n", encoding="utf-8")
        config_path = state_path.parent / "config.yaml"
        config_path.write_bytes(b"config: unchanged\n")

        _log_secret_scan_bypass(tmp_path)

        state = load_yaml(state_path)
        assert state["custom"] == "keep"
        assert state["safety"]["other"] == "keep"
        assert config_path.read_bytes() == b"config: unchanged\n"

    def test_log_secret_scan_bypass_does_not_update_ignore_on_scalar_safety(
        self, tmp_path: Path
    ) -> None:
        from autoskillit.cli._init_helpers import _log_secret_scan_bypass

        state_path = tmp_path / ".autoskillit" / ".state.yaml"
        scalar_safety = b"safety: scalar\n"
        _write_state(state_path, scalar_safety.decode())

        with pytest.raises(TypeError):
            _log_secret_scan_bypass(tmp_path)

        assert state_path.read_bytes() == scalar_safety
        assert not (state_path.parent / ".gitignore").exists()
