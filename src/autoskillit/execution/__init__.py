"""execution/ L1 package: subprocess lifecycle, session parsing, headless runner, testing, DB.

Re-exports the full public surface of the five execution sub-modules.
All sub-modules depend only on autoskillit.core.* at runtime;
execution/headless.py has TYPE_CHECKING-only references to pipeline/.
"""

from autoskillit.execution.db import _execute_readonly_query
from autoskillit.execution.headless import run_headless_core
from autoskillit.execution.process import RealSubprocessRunner, run_managed_async, run_managed_sync
from autoskillit.execution.session import (
    ClaudeSessionResult,
    SkillResult,
    _truncate,
    extract_token_usage,
    parse_session_result,
)
from autoskillit.execution.testing import check_test_passed, parse_pytest_summary

__all__ = [
    # process
    "RealSubprocessRunner",
    "run_managed_async",
    "run_managed_sync",
    # session
    "ClaudeSessionResult",
    "SkillResult",
    "extract_token_usage",
    "parse_session_result",
    "_truncate",
    # headless
    "run_headless_core",
    # testing
    "parse_pytest_summary",
    "check_test_passed",
    # db
    "_execute_readonly_query",
]
