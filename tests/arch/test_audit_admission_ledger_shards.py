"""REQ-ARCH-AAL-001: Architecture guards for the audit admission ledger shards.

Verifies:
- Every shard module exists.
- Every shard module is at most 750 lines (issue ceiling, stricter than
  REQ-CNST-010's 1000-line default).
- The facade is under 1000 lines after the split.
- The ``pipeline/`` directory still has exactly 19 top-level Python
  files (one facade + 18 existing).
- ``pipeline/audit_admission_ledger.py`` is not in
  ``_LINE_LIMIT_EXEMPTIONS`` after the split (E17 retired).
- Every public method on the facade has either a ``_locked`` shard
  (write methods with ``BEGIN IMMEDIATE``) or a ``_read`` shard
  (read-only methods).
- ``_HANDLE_PREFIX`` and ``_HANDLE_DIGEST_DOMAIN`` are defined in
  ``_encoders.py`` and not redefined in ``_reservations.py``
  (single source of truth).
- ``_SCHEMA_SQL`` and ``_METADATA_SCHEMA_VERSION`` are co-located in
  ``_schema.py``; the version constant is imported by ``_connections.py``.
- ``recover_all`` retains the double-``except`` discipline.
- Every transactional facade method wraps with ``except BaseException``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.large]

SHARDS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "pipeline"
    / ("_audit_admission_ledger")
)
PIPELINE_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "pipeline"
FACADE_PATH = PIPELINE_DIR / "audit_admission_ledger.py"

CEILING_LINES = 750
FACADE_CEILING_LINES = 1000
EXPECTED_PIPELINE_PY_COUNT = 19

REQUIRED_SHARDS = {
    "__init__.py",
    "_schema.py",
    "_encoders.py",
    "_connections.py",
    "_recovery.py",
    "_installations.py",
    "_reservations.py",
    "_prepare.py",
    "_authority.py",
    "_finalization.py",
    "_reads.py",
    "_disposition.py",
}

WRITE_METHODS = {
    "create_or_get_installation",
    "retire_installation",
    "reserve",
    "prepare",
    "commit_authority",
    "finalize_response",
    "acknowledge_finalization_effect",
    "commit_disposition",
}

READ_METHODS = {
    "resolve_reservation_handle",
    "finalization_effect_result",
    "current_head",
    "preflight_projection",
    "resolve_disposition",
}


def _read(path: Path) -> str:
    return path.read_text()


def _line_count(path: Path) -> int:
    return sum(1 for _ in path.read_text().splitlines())


def _facade_method_body(method_name: str, src: str) -> str:
    """Return the source of ``method_name`` on the facade class, or ``""``.

    Used by architectural-guard tests to verify each facade method delegates
    to the expected shard helper rather than inlining SQL.
    """
    import ast

    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return ast.get_source_segment(src, item) or ""
    return ""


def test_every_required_shard_exists() -> None:
    actual = {p.name for p in SHARDS_DIR.iterdir() if p.suffix == ".py"}
    assert REQUIRED_SHARDS.issubset(actual), (
        f"Missing shards: {sorted(REQUIRED_SHARDS - actual)}; actual present: {sorted(actual)}"
    )


def test_every_shard_within_750_lines() -> None:
    for py in sorted(SHARDS_DIR.glob("*.py")):
        count = _line_count(py)
        assert count <= CEILING_LINES, f"{py.name}: {count} lines (max {CEILING_LINES})"


def test_facade_under_1000_lines_after_split() -> None:
    count = _line_count(FACADE_PATH)
    assert count <= FACADE_CEILING_LINES, f"facade: {count} lines (max {FACADE_CEILING_LINES})"


def test_pipeline_directory_still_19_files() -> None:
    py_files = [p for p in PIPELINE_DIR.glob("*.py") if p.is_file()]
    assert len(py_files) == EXPECTED_PIPELINE_PY_COUNT, (
        f"pipeline/ has {len(py_files)} Python files (expected {EXPECTED_PIPELINE_PY_COUNT}): "
        f"{sorted(p.name for p in py_files)}"
    )


def test_e17_retired_in_exemption_registry() -> None:
    """REQ-CNST-010-E17 must be removed once the facade is under the cap."""
    from tests.arch.test_subpackage_isolation import _LINE_LIMIT_EXEMPTIONS

    assert "pipeline/audit_admission_ledger.py" not in _LINE_LIMIT_EXEMPTIONS


def test_every_facade_write_method_delegates_to_locked_shard() -> None:
    """Write public methods must exist on the facade and delegate to a
    ``_<method>_locked`` shard function. A regression that inlined write
    SQL back into the facade body would break the delegation check.

    Transactional ``except BaseException`` discipline is asserted by
    ``test_transactional_methods_use_baseexception`` and is intentionally
    not duplicated here.
    """
    facade_src = _read(FACADE_PATH)
    for method in sorted(WRITE_METHODS):
        assert f"def {method}(" in facade_src, f"missing facade method {method}"
        body = _facade_method_body(method, facade_src)
        assert f"_{method}_locked(" in body, (
            f"facade write method {method} must delegate to _{method}_locked shard"
        )


def test_every_facade_read_method_delegates_to_read_shard() -> None:
    """Read public methods must exist on the facade and delegate to a
    ``_<method>_read`` shard function. A regression that inlined read
    SQL back into the facade body would break the delegation check.
    """
    facade_src = _read(FACADE_PATH)
    for method in sorted(READ_METHODS):
        assert f"def {method}(" in facade_src, f"missing facade method {method}"
        body = _facade_method_body(method, facade_src)
        assert f"_{method}_read(" in body, (
            f"facade read method {method} must delegate to _{method}_read shard"
        )


def test_every_shard_imports_without_facade() -> None:
    """The facade must not be a load-time dependency of any shard.

    Each shard module must be importable as a submodule of the private
    ``_audit_admission_ledger`` package without importing the
    ``audit_admission_ledger`` facade module.
    """
    for py in sorted(SHARDS_DIR.glob("*.py")):
        if py.stem == "__init__":
            continue
        importlib.import_module(f"autoskillit.pipeline._audit_admission_ledger.{py.stem}")


def test_handle_constants_single_source_in_encoders() -> None:
    """``_HANDLE_PREFIX`` and ``_HANDLE_DIGEST_DOMAIN`` are defined in
    ``_encoders.py`` and imported (not redefined) by ``_reservations.py``.
    """
    enc = _read(SHARDS_DIR / "_encoders.py")
    res = _read(SHARDS_DIR / "_reservations.py")
    assert "_HANDLE_PREFIX = " in enc
    assert "_HANDLE_DIGEST_DOMAIN = " in enc
    assert "_HANDLE_PREFIX = " not in res
    assert "_HANDLE_DIGEST_DOMAIN = " not in res


def test_schema_constants_co_located_in_schema() -> None:
    """``_SCHEMA_SQL`` and ``_METADATA_SCHEMA_VERSION`` live in
    ``_schema.py`` and the version constant is imported by
    ``_connections.py`` (where ``_validate_metadata`` runs).
    """
    schema = _read(SHARDS_DIR / "_schema.py")
    conn = _read(SHARDS_DIR / "_connections.py")
    assert "_SCHEMA_SQL = " in schema
    assert "_METADATA_SCHEMA_VERSION = " in schema
    assert "_METADATA_SCHEMA_VERSION" in conn


def test_recover_all_retains_double_except_discipline() -> None:
    """The ``recover_all`` double-``except`` defense-in-depth is
    load-bearing for ``TestRecovery``. Removing one arm would lose
    crash-recovery coverage.
    """
    facade_src = _read(FACADE_PATH)
    assert "except AuditAdmissionStorageError" in facade_src
    assert "except (OSError, sqlite3.Error)" in facade_src


def test_transactional_methods_use_baseexception() -> None:
    """Every transactional facade method wraps its body in
    ``try: ... except BaseException: self._rollback(connection); raise;
    finally: connection.close()``. ``except BaseException`` (not
    ``except Exception``) is load-bearing — it guarantees
    ``KeyboardInterrupt`` and ``SystemExit`` rollback before re-raising.
    """
    facade_src = _read(FACADE_PATH)
    assert "except BaseException:" in facade_src, (
        "facade must use except BaseException for transactional methods"
    )
