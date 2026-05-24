"""Split integrity tests for execution/ _session_log_recovery split.

Verifies that symbols moved to _session_log_recovery are importable and
execution gateway still exports all session_log symbols.
"""

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestSessionLogRecoveryModuleExists:
    """Symbols moved to _session_log_recovery are importable from there."""

    def test__recover_crashed_sessions_importable(self):
        from autoskillit.execution._session_log_recovery import recover_crashed_sessions

        assert callable(recover_crashed_sessions)


class TestExecutionGatewayPreserved:
    """execution/ gateway still exports all session_log symbols."""

    def test_flush_session_log_importable(self):
        from autoskillit.execution import flush_session_log

        assert flush_session_log is not None

    def test_recover_crashed_sessions_importable(self):
        from autoskillit.execution import recover_crashed_sessions

        assert callable(recover_crashed_sessions)

    def test_resolve_log_dir_importable(self):
        from autoskillit.execution import resolve_log_dir

        assert callable(resolve_log_dir)
