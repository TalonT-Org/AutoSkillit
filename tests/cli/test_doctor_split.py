"""Structural guards: test_doctor.py split into three files (P1-F02)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ._split_helpers import _has_pytestmark_cli

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_CLI_TESTS = Path(__file__).parent
_CLI_SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "cli"


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


def test_doctor_backend_guards_file_exists():
    """test_doctor_backend_guards.py must exist after the split."""
    assert (_CLI_TESTS / "test_doctor_backend_guards.py").exists()


def test_doctor_backend_guards_has_correct_pytestmark():
    p = _CLI_TESTS / "test_doctor_backend_guards.py"
    assert _has_pytestmark_cli(p), "test_doctor_backend_guards.py missing layer('cli') pytestmark"


def test_doctor_backend_guards_contains_expected_classes():
    p = _CLI_TESTS / "test_doctor_backend_guards.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "TestCheckClaudeProcessStateBreakdown" in class_names
    assert "TestCheckStaleMcpServersBackendGuard" in class_names
    assert "TestCheckMcpServerRegisteredBackendGuard" in class_names
    assert "TestCheckClaudeProcessStateBreakdownBackendGuard" in class_names
    assert "TestRunDoctorBackendWiring" in class_names


def test_doctor_fleet_checks_file_exists():
    """test_doctor_fleet_checks.py must exist after the split."""
    assert (_CLI_TESTS / "test_doctor_fleet_checks.py").exists()


def test_doctor_fleet_checks_has_correct_pytestmark():
    p = _CLI_TESTS / "test_doctor_fleet_checks.py"
    assert _has_pytestmark_cli(p), "test_doctor_fleet_checks.py missing layer('cli') pytestmark"


def test_doctor_fleet_checks_contains_expected_classes():
    p = _CLI_TESTS / "test_doctor_fleet_checks.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "TestGroupMFranchiseDoctorChecks" in class_names
    assert "TestGroupNFeatureGateDoctorChecks" in class_names


def test_doctor_migration_does_not_contain_backend_guard_classes():
    p = _CLI_TESTS / "test_doctor_migration.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for cls in (
        "TestCheckClaudeProcessStateBreakdown",
        "TestCheckStaleMcpServersBackendGuard",
        "TestCheckMcpServerRegisteredBackendGuard",
        "TestCheckClaudeProcessStateBreakdownBackendGuard",
        "TestRunDoctorBackendWiring",
    ):
        assert cls not in class_names, f"{cls} must be moved to test_doctor_backend_guards.py"


def test_doctor_migration_does_not_contain_fleet_classes():
    p = _CLI_TESTS / "test_doctor_migration.py"
    tree = ast.parse(p.read_text())
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for cls in ("TestGroupMFranchiseDoctorChecks", "TestGroupNFeatureGateDoctorChecks"):
        assert cls not in class_names, f"{cls} must be moved to test_doctor_fleet_checks.py"


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
    from autoskillit.cli.doctor._doctor_env import _check_ambient_session_type_skill  # noqa: F401


def test_doctor_submodule_features_importable():
    from autoskillit.cli.doctor._doctor_features import _check_feature_dependencies  # noqa: F401


def test_doctor_submodule_fleet_importable():
    from autoskillit.cli.doctor._doctor_fleet import _check_stale_fleet_state  # noqa: F401
