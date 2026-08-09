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

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_SHARED_READ_SIDE_VALIDATORS = {
    "read_installed_plugin_artifact_identity": (
        "src/autoskillit/core/_plugin_artifact_identity.py"
    ),
    "read_projected_plugin_identity": ("src/autoskillit/workspace/_projection_cache.py"),
}


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
    """AST-scan for modules that call a direct or shared versioned-JSON validator.

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
            call_name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if call_name == "read_versioned_json" or (call_name in _SHARED_READ_SIDE_VALIDATORS):
                rel = str(py_file.relative_to(src_root.parent.parent))
                modules.add(rel)
                break

    return modules


# Documented exceptions: modules that write versioned JSON but do not read it back.
# Rationale for each is in the comment.
_READ_SIDE_EXCEPTIONS: dict[str, str] = {
    "src/autoskillit/core/_plugin_cache.py": (
        "Active-kitchen mutations use the stricter exact-schema registry validator"
    ),
    "src/autoskillit/planner/manifests.py": "Transient artifacts — always same code version",
    "src/autoskillit/planner/compiler.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/planner/merge.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/planner/consolidation.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/planner/validation.py": "Transient single-pipeline-run artifacts",
    "src/autoskillit/execution/_recording_skills.py": "Informational manifest — never read back",
    "src/autoskillit/core/_execution_marker.py": (
        "Progress signal — written and deleted, never read back"
    ),
    "src/autoskillit/execution/session_log.py": (
        "token_usage.json readers use dual-key fallback, not version-gated reading"
    ),
    "src/autoskillit/workspace/session_skills.py": (
        "Per-session consumer metadata is written once and never read by AutoSkillit"
    ),
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
                    f"{module} calls write_versioned_json but no canonical read-side "
                    "validator. Add read_versioned_json or a registered shared validator "
                    "to reads, or add to _READ_SIDE_EXCEPTIONS."
                )

        assert not missing_read, "\n".join(
            f"  {m}: {reason}" for m, reason in missing_read.items()
        )

    def test_read_side_exceptions_are_documented(self):
        """Every entry in _READ_SIDE_EXCEPTIONS must have a non-empty rationale comment."""
        for module, reason in _READ_SIDE_EXCEPTIONS.items():
            assert reason, f"{module} has an empty exception reason — document why it is exempt"

    def test_shared_read_side_validators_delegate_to_versioned_reader(self):
        """Every registered shared validator must itself call read_versioned_json."""
        repo_root = Path(__file__).resolve().parents[2]
        for function_name, relative_path in _SHARED_READ_SIDE_VALIDATORS.items():
            source_path = repo_root / relative_path
            tree = ast.parse(source_path.read_text(), filename=str(source_path))
            function = next(
                (
                    node
                    for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == function_name
                ),
                None,
            )
            assert function is not None, (
                f"shared read-side validator {function_name} is missing from {relative_path}"
            )
            call_names = {
                node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
            }
            assert "read_versioned_json" in call_names, (
                f"{function_name} no longer delegates to read_versioned_json"
            )

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
        """Entries in _READ_SIDE_EXCEPTIONS must be current writer modules.

        Stale exception entries for modules that no longer call write_versioned_json
        indicate dead exception list entries that should be removed.
        """
        writers = _scan_write_versioned_json_callers()
        stale = {m for m in _READ_SIDE_EXCEPTIONS if m not in writers}
        assert not stale, (
            "These modules are in _READ_SIDE_EXCEPTIONS but no longer call "
            "write_versioned_json — remove stale entries: " + ", ".join(sorted(stale))
        )
