"""Package export surface tests for the L1 sub-packages.

Verifies that each L1 package (__init__.py) re-exports the expected
public symbols so callers can use short import paths.
"""

from __future__ import annotations


def test_config_package_exports() -> None:
    from autoskillit.config import AutomationConfig, load_config  # noqa: F401


def test_pipeline_package_exports() -> None:
    from autoskillit.pipeline import (  # noqa: F401
        GATED_TOOLS,
        UNGATED_TOOLS,
        DefaultAuditLog,
        DefaultGateState,
        DefaultTokenLog,
        FailureRecord,
        TokenEntry,
        ToolContext,
        gate_error_result,
    )


def test_execution_package_exports() -> None:
    from autoskillit.execution import (  # noqa: F401
        ClaudeSessionResult,
        SkillResult,
        check_test_passed,
        parse_pytest_summary,
        run_headless_core,
    )


def test_workspace_package_exports() -> None:
    from autoskillit.workspace import (  # noqa: F401
        CleanupResult,
        SkillResolver,
        bundled_skills_dir,
    )


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
    from autoskillit.execution.db import _execute_readonly_query  # noqa: F401


def test_config_settings_all_classes_exported() -> None:
    from autoskillit.config import (  # noqa: F401
        ModelConfig,
        SafetyConfig,
        TestCheckConfig,
    )
