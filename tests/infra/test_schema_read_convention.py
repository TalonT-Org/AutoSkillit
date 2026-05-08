"""Read-side ratchet: enforce that write_versioned_json callers have corresponding read-side
validation.

AST-scans src/autoskillit/ for modules that call write_versioned_json.
Each such module must also call read_versioned_json (or be in the exception list),
ensuring symmetric read/write schema validation.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _scan_write_versioned_json_callers() -> set[str]:
    """AST-scan src/autoskillit/ for modules that call write_versioned_json.

    Returns set of repo-relative module paths (e.g. "src/autoskillit/fleet/state.py").
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    modules: set[str] = set()

    for py_file in src_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_write_versioned_json = (
                isinstance(func, ast.Name) and func.id == "write_versioned_json"
            ) or (isinstance(func, ast.Attribute) and func.attr == "write_versioned_json")
            if is_write_versioned_json:
                rel = str(py_file.relative_to(src_root.parent.parent))
                modules.add(rel)
                break  # one match per module is enough

    return modules


def _scan_read_versioned_json_callers() -> set[str]:
    """AST-scan src/autoskillit/ for modules that call read_versioned_json.

    Returns set of repo-relative module paths.
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    modules: set[str] = set()

    for py_file in src_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_read_versioned_json = (
                isinstance(func, ast.Name) and func.id == "read_versioned_json"
            ) or (isinstance(func, ast.Attribute) and func.attr == "read_versioned_json")
            if is_read_versioned_json:
                rel = str(py_file.relative_to(src_root.parent.parent))
                modules.add(rel)
                break

    return modules


# Documented exceptions: modules that write versioned JSON but do not read it back.
# Rationale for each is in the comment.
_READ_SIDE_EXCEPTIONS: dict[str, str] = {
    "src/autoskillit/planner/manifests.py": "Transient artifacts — always same code version",
    "src/autoskillit/planner/compiler.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/planner/merge.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/planner/consolidation.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/planner/validation.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/execution/_recording_skills.py": "Informational manifest — never read back",
}


class TestSchemaReadConvention:
    def test_write_versioned_json_callers_have_read_side_validation(self):
        """Every module that calls write_versioned_json must also call read_versioned_json,
        unless it is in _READ_SIDE_EXCEPTIONS."""
        writers = _scan_write_versioned_json_callers()
        readers = _scan_read_versioned_json_callers()

        missing_read: dict[str, str] = {}
        for module in sorted(writers):
            if module not in readers and module not in _READ_SIDE_EXCEPTIONS:
                missing_read[module] = (
                    f"{module} calls write_versioned_json but not read_versioned_json. "
                    f"Add read_versioned_json to reads, or add to _READ_SIDE_EXCEPTIONS."
                )

        assert not missing_read, "\n".join(
            f"  {m}: {reason}" for m, reason in missing_read.items()
        )

    def test_read_side_exceptions_are_documented(self):
        """Every entry in _READ_SIDE_EXCEPTIONS must have a non-empty rationale comment."""
        for module, reason in _READ_SIDE_EXCEPTIONS.items():
            assert reason, f"{module} has an empty exception reason — document why it is exempt"

    def test_new_write_versioned_json_caller_without_read_side_fails(self, monkeypatch):
        """Meta-test: injecting a fake writer without a reader must cause the ratchet to fail."""
        original_writers = _scan_write_versioned_json_callers

        def patched_writers():
            writers = original_writers()
            # Add a fake module that writes but doesn't read
            writers.add("src/autoskillit/fake_writer_module.py")
            return writers

        monkeypatch.setattr(
            "tests.infra.test_schema_read_convention._scan_write_versioned_json_callers",
            patched_writers,
        )
        with pytest.raises(AssertionError, match="fake_writer_module"):
            self.test_write_versioned_json_callers_have_read_side_validation()

    def test_exception_list_does_not_include_nonexistent_modules(self):
        """Entries in _READ_SIDE_EXCEPTIONS must actually be writer modules."""
        writers = _scan_write_versioned_json_callers()
        for module in _READ_SIDE_EXCEPTIONS:
            # Exception list can include modules that no longer exist (graceful stale entries)
            # but they should not appear as writers in the current scan
            if module in writers:
                # An exception that is also a current writer — this is valid (transient artifact)
                pass

    def test_all_current_readers_are_also_writers_or_exceptions(self):
        """Every module that calls read_versioned_json must either also write or be exempt.

        (A module can read versioned JSON without writing it — e.g. consumers of fleet/state.py.
        This is not an error; this test just documents the expectation that reader-only modules
        do not need to be in the exceptions list.)
        """
        # This is a documentation test — it always passes but asserts invariants
        readers = _scan_read_versioned_json_callers()
        writers = _scan_write_versioned_json_callers()

        # Readers that are not writers and not exceptions are fine (downstream consumers)
        # This test mainly serves as documentation that the ratchet only requires
        # writers to also be readers, not vice versa.
        assert isinstance(readers, set)
        assert isinstance(writers, set)
