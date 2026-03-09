"""pipeline/ L1 package: audit log, token tracking, gate policy, and ToolContext.

Re-exports the full public surface of the four pipeline sub-modules.
Only pipeline/context.py imports from config/; the other three modules
depend only on autoskillit.core.*.
"""

from autoskillit.core import FailureRecord
from autoskillit.pipeline.audit import (
    COMMAND_MAX_LEN,
    STDERR_MAX_LEN,
    DefaultAuditLog,
)
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import (
    GATE_FILENAME,
    GATED_TOOLS,
    HOOK_CONFIG_FILENAME,
    UNGATED_TOOLS,
    DefaultGateState,
    gate_error_result,
    is_pid_alive,
)
from autoskillit.pipeline.timings import DefaultTimingLog, TimingEntry
from autoskillit.pipeline.tokens import DefaultTokenLog, TokenEntry

__all__ = [
    # audit
    "DefaultAuditLog",
    "FailureRecord",
    "STDERR_MAX_LEN",
    "COMMAND_MAX_LEN",
    # timings
    "DefaultTimingLog",
    "TimingEntry",
    # tokens
    "DefaultTokenLog",
    "TokenEntry",
    # gate
    "DefaultGateState",
    "GATE_FILENAME",
    "GATED_TOOLS",
    "HOOK_CONFIG_FILENAME",
    "UNGATED_TOOLS",
    "gate_error_result",
    "is_pid_alive",
    # context
    "ToolContext",
]
