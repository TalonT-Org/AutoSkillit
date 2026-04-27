"""Structural guards: test_doctor.py split into three files (P1-F02)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_CLI_TESTS = Path(__file__).parent


def _has_pytestmark_cli(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    src = ast.unparse(node.value)
                    return 'layer("cli")' in src
    return False


def test_doctor_scripts_file_exists():
    """test_doctor_scripts.py must exist after the split."""
    assert (_CLI_TESTS / "test_doctor_scripts.py").exists()


def test_doctor_migration_file_exists():
    """test_doctor_migration.py must exist after the split."""
    assert (_CLI_TESTS / "test_doctor_migration.py").exists()


def test_doctor_scripts_has_correct_pytestmark():
    p = _CLI_TESTS / "test_doctor_scripts.py"
    assert _has_pytestmark_cli(p), "test_doctor_scripts.py missing layer('cli') pytestmark"


def test_doctor_migration_has_correct_pytestmark():
    p = _CLI_TESTS / "test_doctor_migration.py"
    assert _has_pytestmark_cli(p), "test_doctor_migration.py missing layer('cli') pytestmark"


def test_doctor_scripts_contains_script_health_class():
    p = _CLI_TESTS / "test_doctor_scripts.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "TestDoctorScriptHealth" in class_names


def test_doctor_migration_contains_quota_cache_class():
    p = _CLI_TESTS / "test_doctor_migration.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "TestCheckQuotaCacheSchema" in class_names


def test_doctor_core_does_not_contain_script_health_class():
    p = _CLI_TESTS / "test_doctor.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "TestDoctorScriptHealth" not in class_names, (
        "TestDoctorScriptHealth must be moved to test_doctor_scripts.py"
    )


def test_doctor_core_does_not_contain_quota_cache_class():
    p = _CLI_TESTS / "test_doctor.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "TestCheckQuotaCacheSchema" not in class_names, (
        "TestCheckQuotaCacheSchema must be moved to test_doctor_migration.py"
    )
