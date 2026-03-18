"""Tests for migration/_api.py."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# T6 — migration/_api.py recipe imports are deferred
# ---------------------------------------------------------------------------


def test_migration_api_recipe_imports_are_deferred() -> None:
    """migration/_api.py must not import recipe/ at module load time."""
    import sys

    # Ensure recipe modules are NOT imported before migration._api
    for key in list(sys.modules.keys()):
        if "autoskillit.recipe" in key:
            del sys.modules[key]

    import importlib

    importlib.import_module("autoskillit.migration._api")

    # recipe imports should not have been triggered at module load
    loaded_recipe = [k for k in sys.modules if k.startswith("autoskillit.recipe")]
    assert not loaded_recipe, (
        f"migration._api loaded recipe modules at import time: {loaded_recipe}"
    )
