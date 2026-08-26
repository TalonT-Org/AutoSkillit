"""REQ-AAL-IMPORTS: Public import-path stability for the audit admission ledger.

The decomposition of ``audit_admission_ledger.py`` into a facade plus a
private ``_audit_admission_ledger/`` sub-package must not change the
public import surface. The canonical path
``from autoskillit.pipeline.audit_admission_ledger import
DefaultAuditAdmissionLedger`` continues to resolve; the sub-package is
private (no re-export of the facade class) so consumers cannot reach
into it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _shard_source(stem: str) -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "autoskillit"
        / "pipeline"
        / "_audit_admission_ledger"
        / f"{stem}.py"
    ).read_text()


def test_facade_path_still_imports_default_ledger() -> None:
    from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger

    assert callable(DefaultAuditAdmissionLedger)


def test_facade_attribute_path_still_accessible() -> None:
    from autoskillit.pipeline import audit_admission_ledger as mod

    assert hasattr(mod, "DefaultAuditAdmissionLedger")


def test_subpackage_does_not_double_as_facade_path() -> None:
    """The private sub-package must not be the canonical import target."""
    sub = importlib.import_module("autoskillit.pipeline._audit_admission_ledger")
    assert not hasattr(sub, "DefaultAuditAdmissionLedger")
    assert sub.__all__ == []


def test_shard_modules_are_importable_without_facade() -> None:
    """Each shard module is loadable as a submodule and does not contain
    a direct import of the facade module.

    The facade is always reachable via the parent package's ``__init__``,
    so a ``sys.modules`` snapshot alone cannot detect a shard's direct
    dependency. The source of each shard is AST-scanned instead.
    """
    facade_module = "autoskillit.pipeline.audit_admission_ledger"
    expected = {
        "_schema",
        "_encoders",
        "_connections",
        "_recovery",
        "_installations",
        "_reservations",
        "_prepare",
        "_authority",
        "_finalization",
        "_reads",
        "_disposition",
    }
    for stem in expected:
        importlib.import_module(f"autoskillit.pipeline._audit_admission_ledger.{stem}")
        tree = ast.parse(_shard_source(stem))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != facade_module, (
                        f"{stem}.py imports facade via 'import {alias.name}'"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module == facade_module:
                raise AssertionError(
                    f"{stem}.py imports facade via 'from {node.module} import ...'"
                )
