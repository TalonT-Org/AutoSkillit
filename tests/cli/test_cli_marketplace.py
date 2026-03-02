"""Tests for the cli/_marketplace.py module."""

from __future__ import annotations


# MK1
def test_marketplace_module_exists():
    pass  # ImportError if missing


# MK2
def test_install_importable_from_marketplace():
    from autoskillit.cli._marketplace import install  # noqa


# MK3
def test_upgrade_importable_from_marketplace():
    from autoskillit.cli._marketplace import upgrade  # noqa


# MK4
def test_ensure_marketplace_importable_from_marketplace():
    from autoskillit.cli._marketplace import _ensure_marketplace  # noqa


# MK5
def test_clear_plugin_cache_importable_from_marketplace():
    from autoskillit.cli._marketplace import _clear_plugin_cache  # noqa


# MK6
def test_install_not_defined_in_app_module():
    """install must have moved — not defined in cli/app.py."""
    import inspect

    import autoskillit.cli.app as app_mod

    src = inspect.getsource(app_mod)
    # The @app.command install definition should NOT be in app.py
    # (only a forwarding import or nothing)
    assert "def install(" not in src, (
        "install() should be defined in cli/_marketplace.py, not cli/app.py"
    )
