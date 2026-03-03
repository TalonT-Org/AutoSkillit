"""Tests for Protocol Contract Layer (GroupB).

All tests in this file are expected to FAIL before implementation and PASS after.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from pathlib import Path

from autoskillit.execution.process import (
    DefaultSubprocessRunner,
    run_managed_async,
    run_managed_sync,
)

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


# ---------------------------------------------------------------------------
# Group D — Public API and Protocol Preservation
# ---------------------------------------------------------------------------


class TestGroupDApiContractPreservation:
    """REQ-API-001..006: Public API contract of execution/process.py is preserved
    after the anyio migration and guarded against future regressions.

    All tests are source-inspection or introspection-based — no subprocesses.
    """

    # ------------------------------------------------------------------
    # REQ-API-001: run_managed_async signature unchanged
    # ------------------------------------------------------------------

    def test_req_api_001_public_params_present(self):
        """run_managed_async exposes the full expected public parameter set."""
        sig = inspect.signature(run_managed_async)
        public_params = {k for k in sig.parameters if not k.startswith("_")}
        expected = {
            "cmd",
            "cwd",
            "timeout",
            "input_data",
            "env",
            "pty_mode",
            "heartbeat_marker",
            "heartbeat_record_types",
            "session_log_dir",
            "completion_marker",
            "stale_threshold",
            "session_record_types",
            "completion_drain_timeout",
            "linux_tracing_config",
        }
        assert expected == public_params, (
            f"run_managed_async public params changed.\n"
            f"  Missing: {expected - public_params}\n"
            f"  Extra:   {public_params - expected}"
        )

    def test_req_api_001_is_coroutine_function(self):
        """run_managed_async must remain an async function."""
        assert inspect.iscoroutinefunction(run_managed_async), (
            "run_managed_async is no longer an async function — "
            "anyio migration violated REQ-API-001"
        )

    def test_req_api_001_public_param_defaults(self):
        """run_managed_async default values for optional public params are unchanged."""
        sig = inspect.signature(run_managed_async)
        p = sig.parameters
        assert p["pty_mode"].default is False
        assert p["heartbeat_marker"].default is None
        assert p["session_log_dir"].default is None
        assert p["input_data"].default is None
        assert p["env"].default is None
        assert p["completion_marker"].default == ""
        assert p["stale_threshold"].default == 1200
        assert p["completion_drain_timeout"].default == 5.0

    # ------------------------------------------------------------------
    # REQ-API-002: DefaultSubprocessRunner satisfies SubprocessRunner protocol
    # ------------------------------------------------------------------

    def test_req_api_002_satisfies_runtime_checkable_protocol(self):
        """DefaultSubprocessRunner() must pass isinstance(SubprocessRunner)."""
        from autoskillit.core.types import SubprocessRunner

        runner = DefaultSubprocessRunner()
        assert isinstance(runner, SubprocessRunner), (
            "DefaultSubprocessRunner no longer satisfies the SubprocessRunner protocol"
        )

    def test_req_api_002_call_is_coroutine_function(self):
        """DefaultSubprocessRunner.__call__ must be async."""
        assert inspect.iscoroutinefunction(DefaultSubprocessRunner.__call__)

    def test_req_api_002_call_params_match_protocol(self):
        """DefaultSubprocessRunner.__call__ exposes the protocol-defined parameter set."""
        sig = inspect.signature(DefaultSubprocessRunner.__call__)
        actual = set(sig.parameters) - {"self"}
        expected = {
            "cmd",
            "cwd",
            "timeout",
            "heartbeat_marker",
            "stale_threshold",
            "completion_marker",
            "session_log_dir",
            "pty_mode",
            "input_data",
            "completion_drain_timeout",
            "linux_tracing_config",
        }
        assert expected == actual, (
            f"DefaultSubprocessRunner.__call__ params changed.\n"
            f"  Missing: {expected - actual}\n"
            f"  Extra:   {actual - expected}"
        )

    # ------------------------------------------------------------------
    # REQ-API-003: run_managed_sync unchanged
    # ------------------------------------------------------------------

    def test_req_api_003_is_sync_not_async(self):
        """run_managed_sync must not be async — it uses subprocess.Popen."""
        assert not inspect.iscoroutinefunction(run_managed_sync)

    def test_req_api_003_params(self):
        """run_managed_sync parameter set is unchanged."""
        sig = inspect.signature(run_managed_sync)
        params = set(sig.parameters)
        expected = {"cmd", "cwd", "timeout", "input_data", "env"}
        assert expected == params, (
            f"run_managed_sync params changed.\n"
            f"  Missing: {expected - params}\n"
            f"  Extra:   {params - expected}"
        )

    def test_req_api_003_uses_popen_not_asyncio(self):
        """run_managed_sync source must reference subprocess.Popen, never asyncio."""
        source = inspect.getsource(run_managed_sync)
        assert "subprocess.Popen" in source, "run_managed_sync no longer uses subprocess.Popen"
        assert "asyncio" not in source, (
            "run_managed_sync must not reference asyncio — it is a sync function"
        )

    # ------------------------------------------------------------------
    # REQ-API-004: Callers require no new asyncio runtime imports
    # ------------------------------------------------------------------

    def _pkg_root(self) -> Path:
        import autoskillit

        return Path(autoskillit.__file__).parent

    def test_req_api_004_llm_triage_no_asyncio_module_import(self):
        """_llm_triage.py must not import asyncio — it delegates to run_managed_async."""
        source = (self._pkg_root() / "_llm_triage.py").read_text()
        tree = ast.parse(source)
        asyncio_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            and any(alias.name == "asyncio" for alias in node.names)
        ]
        assert not asyncio_imports, (
            "_llm_triage.py imported asyncio — callers must not need asyncio after anyio migration"
        )

    def test_req_api_004_headless_subprocess_result_under_type_checking(self):
        """execution/headless.py must import SubprocessResult only under TYPE_CHECKING."""
        source = (self._pkg_root() / "execution" / "headless.py").read_text()
        assert "SubprocessResult" in source, (
            "SubprocessResult reference vanished from headless.py entirely"
        )
        assert "TYPE_CHECKING" in source, "TYPE_CHECKING guard removed from headless.py"
        # A top-level (non-indented) import of SubprocessResult is the violation.
        runtime_import = re.search(
            r"^from\s+\S+\s+import\s+.*SubprocessResult",
            source,
            re.MULTILINE,
        )
        assert runtime_import is None, (
            f"SubprocessResult has a runtime (non-TYPE_CHECKING) import in headless.py: "
            f"{runtime_import.group()!r}"
        )

    def test_req_api_004_server_helpers_subprocess_result_under_type_checking(self):
        """server/helpers.py must import SubprocessResult only under TYPE_CHECKING."""
        source = (self._pkg_root() / "server" / "helpers.py").read_text()
        assert "SubprocessResult" in source, (
            "SubprocessResult reference vanished from server/helpers.py entirely"
        )
        assert "TYPE_CHECKING" in source, "TYPE_CHECKING guard removed from server/helpers.py"
        runtime_import = re.search(
            r"^from\s+\S+\s+import\s+.*SubprocessResult",
            source,
            re.MULTILINE,
        )
        assert runtime_import is None, (
            f"SubprocessResult has a runtime import in server/helpers.py: "
            f"{runtime_import.group()!r}"
        )

    # ------------------------------------------------------------------
    # REQ-API-005: SubprocessResult fields unchanged
    # ------------------------------------------------------------------

    def test_req_api_005_subprocess_result_field_names(self):
        """SubprocessResult must have exactly the 6 canonical fields."""
        from autoskillit.core.types import SubprocessResult

        fields = {f.name for f in dataclasses.fields(SubprocessResult)}
        expected = {"returncode", "stdout", "stderr", "termination", "pid", "channel_confirmation"}
        assert fields == expected, (
            f"SubprocessResult fields changed.\n"
            f"  Missing: {expected - fields}\n"
            f"  Extra:   {fields - expected}"
        )

    def test_req_api_005_channel_confirmation_default(self):
        """SubprocessResult.channel_confirmation defaults to UNMONITORED."""
        from autoskillit.core.types import ChannelConfirmation, SubprocessResult

        field_map = {f.name: f for f in dataclasses.fields(SubprocessResult)}
        assert field_map["channel_confirmation"].default == ChannelConfirmation.UNMONITORED

    # ------------------------------------------------------------------
    # REQ-API-006: TerminationReason and ChannelConfirmation enums unchanged
    # ------------------------------------------------------------------

    def test_req_api_006_termination_reason_members(self):
        """TerminationReason must have exactly the 4 canonical values."""
        from autoskillit.core.types import TerminationReason

        assert set(TerminationReason) == {
            TerminationReason.NATURAL_EXIT,
            TerminationReason.COMPLETED,
            TerminationReason.STALE,
            TerminationReason.TIMED_OUT,
        }

    def test_req_api_006_termination_reason_string_values(self):
        """TerminationReason string values are unchanged (consumed by downstream parsers)."""
        from autoskillit.core.types import TerminationReason

        assert TerminationReason.NATURAL_EXIT == "natural_exit"
        assert TerminationReason.COMPLETED == "completed"
        assert TerminationReason.STALE == "stale"
        assert TerminationReason.TIMED_OUT == "timed_out"

    def test_req_api_006_channel_confirmation_members(self):
        """ChannelConfirmation must have exactly the 3 canonical values."""
        from autoskillit.core.types import ChannelConfirmation

        assert set(ChannelConfirmation) == {
            ChannelConfirmation.CHANNEL_A,
            ChannelConfirmation.CHANNEL_B,
            ChannelConfirmation.UNMONITORED,
        }

    def test_req_api_006_channel_confirmation_string_values(self):
        """ChannelConfirmation string values are unchanged."""
        from autoskillit.core.types import ChannelConfirmation

        assert ChannelConfirmation.CHANNEL_A == "channel_a"
        assert ChannelConfirmation.CHANNEL_B == "channel_b"
        assert ChannelConfirmation.UNMONITORED == "unmonitored"
