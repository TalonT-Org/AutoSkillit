"""Unit tests for the InstalledPluginsFile repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli._installed_plugins import InstalledPluginsFile

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

REAL_STRUCTURE = {
    "version": 2,
    "plugins": {"autoskillit@autoskillit-local": {"name": "autoskillit", "version": "0.8.30"}},
}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_contains_finds_entry_in_nested_plugins(tmp_path: Path) -> None:
    p = tmp_path / "installed_plugins.json"
    _write(p, REAL_STRUCTURE)
    assert InstalledPluginsFile(p).contains("autoskillit@autoskillit-local") is True


def test_contains_does_not_false_positive_on_flat_structure(tmp_path: Path) -> None:
    """A flat dict should NOT be treated as containing the plugin."""
    p = tmp_path / "installed_plugins.json"
    _write(p, {"autoskillit@autoskillit-local": {}})  # flat — wrong format
    # With the real nested structure absent, contains() must return False
    assert InstalledPluginsFile(p).contains("autoskillit@autoskillit-local") is False


def test_contains_returns_false_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "installed_plugins.json"
    _write(p, {"version": 2, "plugins": {}})
    assert InstalledPluginsFile(p).contains("autoskillit@autoskillit-local") is False


def test_contains_returns_false_when_file_absent(tmp_path: Path) -> None:
    p = tmp_path / "no_file.json"
    assert InstalledPluginsFile(p).contains("autoskillit@autoskillit-local") is False


def test_remove_deletes_nested_entry(tmp_path: Path) -> None:
    p = tmp_path / "installed_plugins.json"
    _write(p, REAL_STRUCTURE)
    InstalledPluginsFile(p).remove("autoskillit@autoskillit-local")
    data = json.loads(p.read_text())
    assert "autoskillit@autoskillit-local" not in data.get("plugins", {})


def test_remove_preserves_other_keys(tmp_path: Path) -> None:
    p = tmp_path / "installed_plugins.json"
    payload = {
        "version": 2,
        "plugins": {
            "autoskillit@autoskillit-local": {},
            "other@other-local": {"name": "other"},
        },
    }
    _write(p, payload)
    InstalledPluginsFile(p).remove("autoskillit@autoskillit-local")
    data = json.loads(p.read_text())
    assert data["version"] == 2
    assert "other@other-local" in data["plugins"]


def test_remove_is_noop_when_key_absent(tmp_path: Path) -> None:
    p = tmp_path / "installed_plugins.json"
    _write(p, {"version": 2, "plugins": {}})
    InstalledPluginsFile(p).remove("autoskillit@autoskillit-local")  # should not raise
    data = json.loads(p.read_text())
    assert data == {"version": 2, "plugins": {}}


def test_remove_is_noop_when_file_absent(tmp_path: Path) -> None:
    p = tmp_path / "no_file.json"
    InstalledPluginsFile(p).remove("autoskillit@autoskillit-local")  # should not raise
    assert not p.exists()


def test_installed_plugins_read_logs_on_json_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """_read() emits a WARNING log when installed_plugins.json contains invalid JSON."""
    import logging

    p = tmp_path / "installed_plugins.json"
    p.write_text("{invalid json}")

    store = InstalledPluginsFile(p)
    with caplog.at_level(logging.WARNING, logger="autoskillit.cli._installed_plugins"):
        result = store.get_plugins()
    assert result == {}
    assert any("installed_plugins" in r.message.lower() for r in caplog.records)
