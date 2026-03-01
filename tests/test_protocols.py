"""Tests for Protocol Contract Layer (GroupB).

All tests in this file are expected to FAIL before implementation and PASS after.
"""

# ── Importability ──────────────────────────────────────────────────────────────


def test_nine_protocols_importable_from_core():
    from autoskillit.core import (  # noqa: F401
        AuditStore,
        DatabaseReader,
        GatePolicy,
        HeadlessExecutor,
        MigrationService,
        RecipeRepository,
        TestRunner,
        TokenStore,
        WorkspaceManager,
    )


def test_skillresult_importable_from_core():
    from autoskillit.core import SkillResult  # noqa: F401


def test_cleanupresult_importable_from_core():
    from autoskillit.core import CleanupResult  # noqa: F401


def test_truncate_text_importable_from_core():
    from autoskillit.core import truncate_text

    result = truncate_text("x" * 20, 10)
    assert result.startswith("...[truncated")
    assert result.endswith("x" * 10)
    assert truncate_text("short", 100) == "short"


# ── Backward compatibility ─────────────────────────────────────────────────────


def test_skillresult_still_importable_from_session():
    from autoskillit.execution.session import SkillResult

    assert SkillResult is not None


def test_cleanupresult_still_importable_from_workspace():
    from autoskillit.workspace.cleanup import CleanupResult

    assert CleanupResult is not None


def test_truncate_private_alias_still_available():
    from autoskillit.execution.session import _truncate

    assert _truncate("x" * 20, 10).startswith("...[truncated")


# ── Runtime-checkable ──────────────────────────────────────────────────────────


def test_all_new_protocols_are_runtime_checkable():
    from autoskillit.core import (
        AuditStore,
        DatabaseReader,
        GatePolicy,
        HeadlessExecutor,
        MigrationService,
        RecipeRepository,
        TestRunner,
        TokenStore,
        WorkspaceManager,
    )

    for proto in [
        GatePolicy,
        AuditStore,
        TokenStore,
        TestRunner,
        HeadlessExecutor,
        RecipeRepository,
        MigrationService,
        DatabaseReader,
        WorkspaceManager,
    ]:
        assert getattr(proto, "_is_runtime_protocol", False), (
            f"{proto.__name__} not decorated with @runtime_checkable"
        )


# ── isinstance checks — existing classes satisfy protocols ─────────────────────


def test_defaultgatestate_satisfies_gatepolicy():
    from autoskillit.core import GatePolicy
    from autoskillit.pipeline.gate import DefaultGateState

    assert isinstance(DefaultGateState(), GatePolicy)


def test_defaultauditlog_satisfies_auditstore():
    from autoskillit.core import AuditStore
    from autoskillit.pipeline.audit import DefaultAuditLog

    assert isinstance(DefaultAuditLog(), AuditStore)


def test_defaulttokenlog_satisfies_tokenstore():
    from autoskillit.core import TokenStore
    from autoskillit.pipeline.tokens import DefaultTokenLog

    assert isinstance(DefaultTokenLog(), TokenStore)


# ── isinstance checks — Default* classes satisfy protocols ─────────────────────


def test_default_workspace_manager_satisfies_workspace_manager():
    from autoskillit.core import WorkspaceManager
    from autoskillit.workspace.cleanup import DefaultWorkspaceManager

    assert isinstance(DefaultWorkspaceManager(), WorkspaceManager)


def test_default_database_reader_satisfies_database_reader():
    from autoskillit.core import DatabaseReader
    from autoskillit.execution.db import DefaultDatabaseReader

    assert isinstance(DefaultDatabaseReader(), DatabaseReader)


def test_default_recipe_repository_satisfies_recipe_repository():
    from autoskillit.core import RecipeRepository
    from autoskillit.recipe.repository import DefaultRecipeRepository

    assert isinstance(DefaultRecipeRepository(), RecipeRepository)


def test_default_migration_service_satisfies_migration_service():
    from autoskillit.core import MigrationService
    from autoskillit.migration.engine import DefaultMigrationService, MigrationEngine

    assert isinstance(DefaultMigrationService(MigrationEngine([])), MigrationService)


def test_default_test_runner_satisfies_test_runner():
    from unittest.mock import MagicMock

    from autoskillit.core import TestRunner
    from autoskillit.execution.testing import DefaultTestRunner

    mock_config = MagicMock()
    mock_config.test_check.command = ["task", "test-all"]
    mock_config.test_check.timeout = 600
    assert isinstance(DefaultTestRunner(mock_config, MagicMock()), TestRunner)


def test_default_headless_executor_satisfies_headless_executor():
    from unittest.mock import MagicMock

    from autoskillit.core import HeadlessExecutor
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    assert isinstance(DefaultHeadlessExecutor(MagicMock()), HeadlessExecutor)


# ── GateState mutation (REQ-PROTO-009) ────────────────────────────────────────


def test_defaultgatestate_enable_method():
    from autoskillit.pipeline.gate import DefaultGateState

    gs = DefaultGateState()
    assert not gs.enabled
    gs.enable()
    assert gs.enabled


def test_defaultgatestate_disable_method():
    from autoskillit.pipeline.gate import DefaultGateState

    gs = DefaultGateState(enabled=True)
    gs.disable()
    assert not gs.enabled


def test_defaultgatestate_direct_mutation_allowed():
    from autoskillit.pipeline.gate import DefaultGateState

    gs = DefaultGateState()
    gs.enabled = True  # must not raise FrozenInstanceError
    assert gs.enabled


# ── core/__init__ __all__ completeness ────────────────────────────────────────


def test_all_ten_protocols_in_core_all():
    import autoskillit.core as core

    expected_protocols = {
        "SubprocessRunner",
        "GatePolicy",
        "AuditStore",
        "TokenStore",
        "TestRunner",
        "HeadlessExecutor",
        "RecipeRepository",
        "MigrationService",
        "DatabaseReader",
        "WorkspaceManager",
    }
    assert expected_protocols <= set(core.__all__)


def test_moved_types_in_core_all():
    import autoskillit.core as core

    assert "SkillResult" in core.__all__
    assert "CleanupResult" in core.__all__
    assert "truncate_text" in core.__all__
