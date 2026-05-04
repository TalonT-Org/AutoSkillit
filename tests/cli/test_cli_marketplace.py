"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# MK1
def test_marketplace_module_exists():
    pass  # ImportError if missing


# MK2
def test_install_importable_from_marketplace():
    from autoskillit.cli._marketplace import install  # noqa: F401


# MK3
def test_upgrade_importable_from_marketplace():
    from autoskillit.cli._marketplace import upgrade  # noqa: F401


# MK4
def test_ensure_marketplace_importable_from_marketplace():
    from autoskillit.cli._marketplace import _ensure_marketplace  # noqa: F401


# MK5
def test_clear_plugin_cache_importable_from_marketplace():
    from autoskillit.cli._marketplace import _clear_plugin_cache  # noqa: F401


# MK6
def test_install_defined_in_app_module():
    """install command is registered in cli/app.py as a thin @app.command wrapper."""
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")
    src = inspect.getsource(app_mod)
    assert "def install(" in src


# MK-DEP-1
def test_install_registered_as_cli_command():
    """autoskillit install is a registered CLI command (delegates to _marketplace)."""
    from autoskillit import cli

    assert hasattr(cli, "install")


# MK-DEP-2
def test_upgrade_is_registered_as_cli_command():
    """autoskillit upgrade must be a registered CLI command (defined in cli/app.py)."""
    import importlib
    import inspect

    app_mod = importlib.import_module("autoskillit.cli.app")
    src = inspect.getsource(app_mod)
    assert "def upgrade(" in src


# MK-DEP-3
def test_marketplace_module_still_importable():
    """_marketplace module is still importable (not deleted)."""
    import autoskillit.cli._marketplace  # noqa: F401


# MK-GUARD-1
def test_install_guards_same_version_when_kitchen_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """install() must skip _clear_plugin_cache when a kitchen is open for the current project."""
    from autoskillit.cli._marketplace import install

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("autoskillit.cli._marketplace.shutil.which", lambda cmd: "/usr/bin/claude")
    monkeypatch.setattr("autoskillit.cli._marketplace._ensure_marketplace", lambda: tmp_path)
    monkeypatch.setattr("autoskillit.cli._marketplace._ensure_workspace_ready", lambda: None)

    @contextlib.contextmanager
    def _fake_lock():
        yield

    monkeypatch.setattr("autoskillit.core._InstallLock", _fake_lock)
    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: True)

    clear_called: list[bool] = []
    monkeypatch.setattr(
        "autoskillit.cli._marketplace._clear_plugin_cache",
        lambda: clear_called.append(True),
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.generate_hooks_json", lambda: {})
    monkeypatch.setattr("autoskillit.cli._marketplace.atomic_write", lambda *a, **kw: None)
    monkeypatch.setattr("autoskillit.cli._marketplace.pkg_root", lambda: tmp_path)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.evict_direct_mcp_entry", lambda *a: False)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.sweep_all_scopes_for_orphans", lambda *a: None
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.sync_hooks_to_settings", lambda *a: None)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace._user_claude_json_path", lambda: tmp_path / "claude.json"
    )
    monkeypatch.setattr(
        "autoskillit.cli._claude_settings_path", lambda *a: tmp_path / "settings.json"
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.invalidate_fetch_cache", lambda *a: None
    )

    install()

    assert not clear_called, "_clear_plugin_cache must be skipped when kitchen is open"
    out = capsys.readouterr().out
    assert "kitchen" in out.lower(), "install must print a notification when skipping cache clear"


# MK-GUARD-2
def test_install_warns_on_version_mismatch_with_kitchen_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """install() must emit WARNING with version strings when kitchen open and versions differ."""
    from autoskillit.cli._marketplace import install

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("autoskillit.cli._marketplace.shutil.which", lambda cmd: "/usr/bin/claude")
    monkeypatch.setattr("autoskillit.cli._marketplace._ensure_marketplace", lambda: tmp_path)
    monkeypatch.setattr("autoskillit.cli._marketplace._ensure_workspace_ready", lambda: None)

    @contextlib.contextmanager
    def _fake_lock():
        yield

    monkeypatch.setattr("autoskillit.core._InstallLock", _fake_lock)
    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: True)
    monkeypatch.setattr(
        "autoskillit.version.version_info",
        lambda **kw: {
            "match": False,
            "plugin_json_version": "0.9.347",
            "package_version": "0.9.351",
        },
    )

    clear_called: list[bool] = []
    monkeypatch.setattr(
        "autoskillit.cli._marketplace._clear_plugin_cache",
        lambda: clear_called.append(True),
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.generate_hooks_json", lambda: {})
    monkeypatch.setattr("autoskillit.cli._marketplace.atomic_write", lambda *a, **kw: None)
    monkeypatch.setattr("autoskillit.cli._marketplace.pkg_root", lambda: tmp_path)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )
    monkeypatch.setattr("autoskillit.hook_registry.validate_plugin_cache_hooks", lambda **kw: [])
    monkeypatch.setattr("autoskillit.cli._marketplace.evict_direct_mcp_entry", lambda *a: False)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.sweep_all_scopes_for_orphans", lambda *a: None
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.sync_hooks_to_settings", lambda *a: None)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace._user_claude_json_path", lambda: tmp_path / "claude.json"
    )
    monkeypatch.setattr(
        "autoskillit.cli._claude_settings_path", lambda *a: tmp_path / "settings.json"
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.invalidate_fetch_cache", lambda *a: None
    )

    install()

    out = capsys.readouterr().out
    assert "WARNING" in out, "install must emit WARNING when kitchen open and version mismatch"
    assert "0.9.347" in out, "install must include cached version in WARNING"
    assert "0.9.351" in out, "install must include installed version in WARNING"
    assert not clear_called, "_clear_plugin_cache must not be called when kitchen is open"
