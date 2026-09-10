"""REQ-HOOKS-005: Dual-import contract for stdlib-only hook authority modules.

``_session_binding.py`` and ``_join_ledger.py`` are imported under two distinct
identities:

- Bare-name ``_session_binding`` / ``_join_ledger`` — by hook subprocesses that
  bootstrap ``sys.path`` with ``src/autoskillit/hooks/``.
- Absolute ``autoskillit.hooks._session_binding`` / ``autoskillit.hooks._join_ledger``
  — by in-venv callers inside the package venv.

The two identities exchange only serialized JSON and filesystem paths, never
Python class instances (per the module docstrings). The
``register_module_aliases`` helper in ``_capture/_module_identity.py`` provides
the canonical mechanism for converging both spellings onto one ``sys.modules``
entry.

These tests guard that contract: if a future change breaks either identity,
the tests fail.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks")


@pytest.fixture(autouse=True, scope="session")
def _hooks_dir_on_syspath() -> None:  # pyright: ignore[reportUnusedFunction]
    """Ensure bare-name imports like ``_session_binding`` resolve in test worker.

    Each test worker has its own ``sys.modules`` namespace, so prepending the
    hooks directory is safe under xdist and idempotent across repeated calls.
    """
    if _HOOKS_DIR not in sys.path:
        sys.path.insert(0, _HOOKS_DIR)


def _fresh_dual_import(absolute_name: str, bare_name: str) -> tuple[object, object]:
    """Drop both spellings from ``sys.modules`` then re-import them.

    Returns ``(absolute_module, bare_module)``. After re-import, the bare-name
    identity is converged onto the absolute identity via
    ``register_module_aliases`` so ``bare is absolute``.
    """
    from autoskillit.hooks._capture._module_identity import register_module_aliases

    sys.modules.pop(absolute_name, None)
    sys.modules.pop(bare_name, None)

    absolute = importlib.import_module(absolute_name)
    register_module_aliases(absolute_name)
    bare = importlib.import_module(bare_name)

    return absolute, bare


def test_session_binding_dual_import_contract() -> None:
    """REQ-HOOKS-005: ``_session_binding`` must resolve under both identities.

    The module docstring (``hooks/_session_binding.py:1-7``) explicitly documents
    the two-identity design made safe by statelessness. The bare-name identity
    used by hook subprocesses and the absolute identity used by in-venv callers
    must converge to the same ``sys.modules`` entry.
    """
    absolute, bare = _fresh_dual_import(
        "autoskillit.hooks._session_binding",
        "_session_binding",
    )

    assert bare is absolute, (
        "bare-name import `_session_binding` must resolve to the same module "
        "object as the absolute import `autoskillit.hooks._session_binding` "
        "(dual-import contract per hooks/_session_binding.py:1-7)"
    )

    # Documented statelessness invariant: the module carries no mutable
    # module-level state, so the two identities can safely share one object.
    assert not hasattr(bare, "_mutable_state"), (
        "_session_binding must remain stateless at module level — adding "
        "mutable module-level state would break the dual-import contract."
    )


def test_join_ledger_dual_import_contract() -> None:
    """REQ-HOOKS-005: ``_join_ledger`` must resolve under both identities.

    Mirror of ``test_session_binding_dual_import_contract`` for the join-ledger
    authority. The module docstring (``hooks/_join_ledger.py:1-6``) documents
    the same dual-import contract.
    """
    absolute, bare = _fresh_dual_import(
        "autoskillit.hooks._join_ledger",
        "_join_ledger",
    )

    assert bare is absolute, (
        "bare-name import `_join_ledger` must resolve to the same module "
        "object as the absolute import `autoskillit.hooks._join_ledger` "
        "(dual-import contract per hooks/_join_ledger.py:1-6)"
    )

    assert not hasattr(bare, "_mutable_state"), (
        "_join_ledger must remain stateless at module level — adding "
        "mutable module-level state would break the dual-import contract."
    )
