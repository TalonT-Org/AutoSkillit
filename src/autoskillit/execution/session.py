"""Domain model for Claude Code headless session results.

L2 module: imports only from L0 (types, _logging). No server side-effects.
Centralizes all session-parsing concerns so callers can work with typed
objects instead of raw JSON strings.

Facade: re-exports from _session_model, _session_content, _retry_fsm, and
_session_outcome sub-modules.
"""

from __future__ import annotations

from autoskillit.core import (
    CliSubtype,
    SkillResult,
    get_logger,
    truncate_text,
)
from autoskillit.execution._retry_fsm import (
    _KILL_ANOMALY_SUBTYPES,  # noqa: F401 — re-export for callers
    _compute_retry,  # noqa: F401 — re-export for callers
    _is_kill_anomaly,  # noqa: F401 — re-export for callers
)
from autoskillit.execution._session_content import (
    _check_expected_patterns,  # noqa: F401 — re-export for callers
    _check_session_content,  # noqa: F401 — re-export for callers
    _evaluate_content_state,  # noqa: F401 — re-export for callers
)
from autoskillit.execution._session_model import (
    FAILURE_SUBTYPES,  # noqa: F401 — re-export for callers
    ClaudeSessionResult,  # noqa: F401 — re-export for callers
    ContentState,  # noqa: F401 — re-export for callers
    extract_token_usage,  # noqa: F401 — re-export for callers
    parse_session_result,  # noqa: F401 — re-export for callers
)
from autoskillit.execution._session_outcome import (
    _compute_outcome,  # noqa: F401 — re-export for callers
    _compute_success,  # noqa: F401 — re-export for callers
)

logger = get_logger(__name__)
_truncate = truncate_text
# Re-export SkillResult so existing callers can import from this module.
__all__ = ["CliSubtype", "SkillResult"]
