"""execution/ L1 package: subprocess lifecycle, session parsing, headless runner, testing, DB.

Re-exports the full public surface of the six execution sub-modules.
All sub-modules depend only on autoskillit.core.* at runtime;
execution/headless.py has TYPE_CHECKING-only references to pipeline/.
"""

from autoskillit.core import SkillResult
from autoskillit.execution.db import DefaultDatabaseReader, _execute_readonly_query
from autoskillit.execution.headless import DefaultHeadlessExecutor, run_headless_core
from autoskillit.execution.process import RealSubprocessRunner, run_managed_async, run_managed_sync
from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed
from autoskillit.execution.session import (
    ClaudeSessionResult,
    extract_token_usage,
    parse_session_result,
)
from autoskillit.execution.testing import (
    DefaultTestRunner,
    check_test_passed,
    parse_pytest_summary,
)

execute_readonly_query = _execute_readonly_query

__all__ = [
    # process
    "RealSubprocessRunner",
    "run_managed_async",
    "run_managed_sync",
    # quota
    "QuotaStatus",
    "check_and_sleep_if_needed",
    # session
    "ClaudeSessionResult",
    "SkillResult",
    "extract_token_usage",
    "parse_session_result",
    # headless
    "run_headless_core",
    "DefaultHeadlessExecutor",
    # testing
    "parse_pytest_summary",
    "check_test_passed",
    "DefaultTestRunner",
    # db
    "execute_readonly_query",
    "DefaultDatabaseReader",
]
