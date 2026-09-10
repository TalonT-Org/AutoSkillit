"""Gateway regression tests for the #4671 core/ and recipe/ decomposition.

The frozen snapshots in ``_core_gateway_snapshot.py`` and
``_recipe_gateway_snapshot.py`` each promise, in their module docstrings, that
every captured name "must remain accessible via ``hasattr``" on its package.
Before this module existed the snapshots were referenced by zero tests, so that
promise was unenforced — a dead regression anchor that implied coverage which
did not exist.

These tests are the enforcement. They are the reason a phase of the
decomposition cannot silently drop a public name: the gateway surface of
``autoskillit.core`` and ``autoskillit.recipe`` is driven by indirection
(``lazy_loader`` reading ``core/__init__.pyi`` for core, explicit submodule
binding plus re-exports for recipe), so a lost symbol produces an ImportError
at a downstream call site rather than a failure at the move site.
"""

from __future__ import annotations

import pytest

from tests.arch._core_gateway_snapshot import CORE_GATEWAY_SYMBOLS
from tests.arch._helpers import SRC_ROOT
from tests.arch._recipe_gateway_snapshot import RECIPE_GATEWAY_SYMBOLS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_core_gateway_snapshot_symbols_all_resolve() -> None:
    """Every name frozen in CORE_GATEWAY_SYMBOLS resolves on autoskillit.core.

    ``core/__init__.py`` is a 35-line ``lazy_loader`` facade driven by
    ``core/__init__.pyi``. A symbol absent from the stub becomes invisible to
    the loader and raises AttributeError/ImportError on first access, so the
    stub and the runtime surface must stay in lockstep. This asserts the
    runtime half of that contract.
    """
    import autoskillit.core

    missing = sorted(name for name in CORE_GATEWAY_SYMBOLS if not hasattr(autoskillit.core, name))
    assert not missing, (
        f"{len(missing)} symbol(s) frozen in CORE_GATEWAY_SYMBOLS no longer resolve on "
        f"autoskillit.core — the #4671 decomposition dropped part of the public gateway "
        f"surface. Either restore the re-export (likely a missing entry in "
        f"core/__init__.pyi) or, if the removal is intentional, update the snapshot: {missing}"
    )


def test_recipe_gateway_snapshot_symbols_all_resolve() -> None:
    """Every name frozen in RECIPE_GATEWAY_SYMBOLS resolves on autoskillit.recipe.

    ``recipe/__init__.py`` binds each pre-Part-D submodule shim explicitly,
    because Python only exposes a submodule as a parent-package attribute when
    the parent triggers the load. Dropping one of those binding imports would
    silently remove ``autoskillit.recipe.<old_name>`` for existing callers.
    """
    import autoskillit.recipe

    missing = sorted(
        name for name in RECIPE_GATEWAY_SYMBOLS if not hasattr(autoskillit.recipe, name)
    )
    assert not missing, (
        f"{len(missing)} symbol(s) frozen in RECIPE_GATEWAY_SYMBOLS no longer resolve on "
        f"autoskillit.recipe — the Part D decomposition dropped part of the public gateway "
        f"surface. Either restore the re-export or submodule binding in recipe/__init__.py "
        f"or, if the removal is intentional, update the snapshot: {missing}"
    )


def test_snapshots_are_non_trivial() -> None:
    """Guard the guard: an emptied snapshot would make the tests above vacuous."""
    assert len(CORE_GATEWAY_SYMBOLS) > 1000, (
        f"CORE_GATEWAY_SYMBOLS holds {len(CORE_GATEWAY_SYMBOLS)} names; the captured core "
        f"gateway surface is ~1136. A collapse this large means the snapshot was truncated, "
        f"which would silently neuter test_core_gateway_snapshot_symbols_all_resolve."
    )
    assert len(RECIPE_GATEWAY_SYMBOLS) > 200, (
        f"RECIPE_GATEWAY_SYMBOLS holds {len(RECIPE_GATEWAY_SYMBOLS)} names; the captured "
        f"recipe gateway surface is ~262. A collapse this large means the snapshot was "
        f"truncated, which would silently neuter "
        f"test_recipe_gateway_snapshot_symbols_all_resolve."
    )


def test_no_module_shadows_a_sibling_package() -> None:
    """No ``pkg.py`` may sit beside a ``pkg/`` package under src/autoskillit.

    When both exist Python always resolves ``import ...pkg`` to the package, so
    the module is unreachable by any import — dead code whose docstring
    typically claims to preserve a path it cannot serve. Phase A left
    ``core/io.py`` beside ``core/io/`` and Part D left ``recipe/contracts.py``
    beside ``recipe/contracts/`` in exactly that state; both were deleted.
    """
    offenders = sorted(
        f"{directory.relative_to(SRC_ROOT)}/ shadowed by {directory.name}.py"
        for directory in SRC_ROOT.rglob("*")
        if directory.is_dir()
        and directory.name != "__pycache__"
        and (directory.parent / f"{directory.name}.py").exists()
    )
    assert not offenders, (
        "A module sits beside a same-named package, making the module unreachable by "
        "any import (the package always wins). Delete the shadowed module or rename "
        "one of the two:\n  " + "\n  ".join(offenders)
    )
