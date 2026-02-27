"""Package export surface tests for the L1 sub-packages.

Verifies that each L1 package (__init__.py) re-exports the expected
public symbols so callers can use short import paths.
"""

from __future__ import annotations


def test_config_package_exports() -> None:
    from autoskillit.config import AutomationConfig, load_config

    assert AutomationConfig is not None
    assert load_config is not None


def test_pipeline_package_exports() -> None:
    from autoskillit.pipeline import (
        GATED_TOOLS,
        UNGATED_TOOLS,
        AuditLog,
        FailureRecord,
        GateState,
        TokenEntry,
        TokenLog,
        ToolContext,
        gate_error_result,
    )

    assert AuditLog is not None
    assert FailureRecord is not None
    assert TokenLog is not None
    assert TokenEntry is not None
    assert GateState is not None
    assert GATED_TOOLS is not None
    assert UNGATED_TOOLS is not None
    assert gate_error_result is not None
    assert ToolContext is not None


def test_execution_package_exports() -> None:
    from autoskillit.execution import (
        ClaudeSessionResult,
        SkillResult,
        check_test_passed,
        parse_pytest_summary,
        run_headless_core,
    )

    assert SkillResult is not None
    assert ClaudeSessionResult is not None
    assert run_headless_core is not None
    assert check_test_passed is not None
    assert parse_pytest_summary is not None


def test_workspace_package_exports() -> None:
    from autoskillit.workspace import CleanupResult, SkillResolver, bundled_skills_dir

    assert CleanupResult is not None
    assert SkillResolver is not None
    assert bundled_skills_dir is not None


def test_failure_record_in_core_types() -> None:
    from autoskillit.core.types import FailureRecord

    r = FailureRecord(
        timestamp="t",
        skill_command="c",
        exit_code=1,
        subtype="s",
        needs_retry=False,
        retry_reason="",
        stderr="",
    )
    assert r.exit_code == 1
    assert r.to_dict()["exit_code"] == 1


def test_failure_record_re_exported_from_pipeline() -> None:
    """FailureRecord is re-exported from pipeline/__init__.py for convenience."""
    from autoskillit.core.types import FailureRecord as CoreFailureRecord
    from autoskillit.pipeline import FailureRecord

    assert FailureRecord is CoreFailureRecord


def test_execution_db_export() -> None:
    from autoskillit.execution import _execute_readonly_query

    assert _execute_readonly_query is not None


def test_config_settings_all_classes_exported() -> None:
    from autoskillit.config import (
        ModelConfig,
        SafetyConfig,
        TestCheckConfig,
    )

    assert TestCheckConfig is not None
    assert SafetyConfig is not None
    assert ModelConfig is not None
