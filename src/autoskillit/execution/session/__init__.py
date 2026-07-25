"""Typed headless-session result facade.

IL-1 module with no server side effects.
"""

from __future__ import annotations

from autoskillit.core import CliSubtype, SkillResult, get_logger, truncate_text
from autoskillit.execution.session._exit_classification import (
    classify_infra_exit,  # noqa: F401 — re-export for callers
    has_rate_limit_signal,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.session._retry_fsm import (
    _KILL_ANOMALY_SUBTYPES,  # noqa: F401 — re-export for callers
    _compute_retry,  # noqa: F401 — re-export for callers
    _is_kill_anomaly,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.session._session_content import (
    _check_expected_patterns,  # noqa: F401 — re-export for callers
    _check_session_content,  # noqa: F401 — re-export for callers
    _collapse_hr_split_delimiters,  # noqa: F401 — re-export for callers
    _evaluate_content_state,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.session._session_model import (
    FAILURE_SUBTYPES,  # noqa: F401 — re-export for callers
    ClaudeSessionResult,  # noqa: F401 — re-export for callers
    ContentState,  # noqa: F401 — re-export for callers
    extract_token_usage,  # noqa: F401 — re-export for callers
    parse_session_result,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.session._session_outcome import (
    _compute_outcome,  # noqa: F401 — re-export for callers
    _compute_success,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.session._session_state import (
    SessionState,  # noqa: F401 — re-export for callers
    SessionStateLock,  # noqa: F401 — re-export for callers
    clear_session_state,  # noqa: F401 — re-export for callers
    persist_session_state,  # noqa: F401 — re-export for callers
    read_session_state,  # noqa: F401 — re-export for callers
)
from autoskillit.execution.session._skill_session_contract_store import (
    DefaultSkillSessionContractStore,
    SkillSessionContract,
    StoredSkillSessionContract,
    delete_skill_session_contracts,
)

logger = get_logger(__name__)
_truncate = truncate_text
# Re-export SkillResult so existing callers can import from this module.
__all__ = [
    "CliSubtype",
    "DefaultSkillSessionContractStore",
    "SessionState",
    "SessionStateLock",
    "SkillSessionContract",
    "SkillResult",
    "StoredSkillSessionContract",
    "classify_infra_exit",
    "delete_skill_session_contracts",
    "has_rate_limit_signal",
    "clear_session_state",
    "persist_session_state",
    "read_session_state",
]
