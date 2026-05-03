"""Structural guards: test_doctor.py split into three files (P1-F02)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_CLI_TESTS = Path(__file__).parent
_CLI_SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"


def _has_pytestmark_cli(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    src = ast.unparse(node.value)
                    return 'layer("cli")' in src or "layer('cli')" in src
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


def test_doctor_facade_exports_run_doctor():
    from autoskillit.cli.doctor import run_doctor  # noqa: F401


def test_doctor_submodule_types_importable():
    from autoskillit.cli.doctor._doctor_types import _NON_PROBLEM, DoctorResult  # noqa: F401


def test_doctor_submodule_mcp_importable():
    from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered  # noqa: F401


def test_doctor_submodule_hooks_importable():
    from autoskillit.cli.doctor._doctor_hooks import _check_hook_registry_drift  # noqa: F401


def test_doctor_submodule_install_importable():
    from autoskillit.cli.doctor._doctor_install import _check_stale_entry_points  # noqa: F401


def test_doctor_submodule_config_importable():
    from autoskillit.cli.doctor._doctor_config import (
        _check_config_layers_for_secrets,  # noqa: F401
    )


def test_doctor_submodule_runtime_importable():
    from autoskillit.cli.doctor._doctor_runtime import _check_quota_cache_schema  # noqa: F401


def test_doctor_submodule_env_importable():
    from autoskillit.cli.doctor._doctor_env import _check_ambient_session_type_leaf  # noqa: F401


def test_doctor_submodule_features_importable():
    from autoskillit.cli.doctor._doctor_features import _check_feature_dependencies  # noqa: F401


def test_doctor_submodule_fleet_importable():
    from autoskillit.cli.doctor._doctor_fleet import _check_stale_fleet_state  # noqa: F401
