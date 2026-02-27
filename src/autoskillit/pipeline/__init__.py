"""pipeline/ L1 package: audit log, token tracking, gate policy, and ToolContext.

Re-exports the full public surface of the four pipeline sub-modules.
Only pipeline/context.py imports from config/; the other three modules
depend only on autoskillit.core.*.
"""

from autoskillit.core.types import FailureRecord
from autoskillit.pipeline.audit import (
    COMMAND_MAX_LEN,
    STDERR_MAX_LEN,
    AuditLog,
    _audit_log,
)
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import (
    GATED_TOOLS,
    UNGATED_TOOLS,
    GateState,
    gate_error_result,
)
from autoskillit.pipeline.tokens import TokenEntry, TokenLog, _token_log

__all__ = [
    # audit
    "AuditLog",
    "FailureRecord",
    "_audit_log",
    "STDERR_MAX_LEN",
    "COMMAND_MAX_LEN",
    # tokens
    "TokenLog",
    "TokenEntry",
    "_token_log",
    # gate
    "GateState",
    "GATED_TOOLS",
    "UNGATED_TOOLS",
    "gate_error_result",
    # context
    "ToolContext",
]
