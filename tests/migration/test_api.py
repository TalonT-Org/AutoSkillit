"""Tests for migration/_api.py."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# T6 — migration/_api.py recipe imports are deferred
# ---------------------------------------------------------------------------


def test_migration_api_recipe_imports_are_deferred() -> None:
    """migration/_api.py must not import recipe/ at module load time."""
    import importlib
    import sys

    # Save then remove all autoskillit.recipe and migration._api modules so we can
    # test a cold import without corrupting the worker's sys.modules state (xdist safety).
    saved: dict[str, object] = {}
    to_remove = [
        k for k in sys.modules if "autoskillit.recipe" in k or k == "autoskillit.migration._api"
    ]
    for key in to_remove:
        saved[key] = sys.modules.pop(key)

    try:
        importlib.import_module("autoskillit.migration._api")

        # recipe imports should not have been triggered at module load
        loaded_recipe = [k for k in sys.modules if k.startswith("autoskillit.recipe")]
        assert not loaded_recipe, (
            f"migration._api loaded recipe modules at import time: {loaded_recipe}"
        )
    finally:
        # Remove any modules loaded during this test (cold imports are disposable)
        for key in list(sys.modules.keys()):
            if "autoskillit.recipe" in key or key == "autoskillit.migration._api":
                del sys.modules[key]
        # Restore original modules to preserve class identity for other tests in this worker
        sys.modules.update(saved)
