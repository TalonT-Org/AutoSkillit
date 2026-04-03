"""pipeline/ L1 package: audit log, token tracking, gate policy, and ToolContext.

Re-exports the full public surface of the four pipeline sub-modules.
Only pipeline/context.py imports from config/; the other three modules
depend only on autoskillit.core.*.
"""

from autoskillit.core import FailureRecord, is_protected_branch
from autoskillit.pipeline.audit import (
    COMMAND_MAX_LEN,
    STDERR_MAX_LEN,
    DefaultAuditLog,
)
from autoskillit.pipeline.background import DefaultBackgroundSupervisor, write_status
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import (
    GATED_TOOLS,
    UNGATED_TOOLS,
    DefaultGateState,
    gate_error_result,
    headless_error_result,
)
from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog, McpResponseEntry
from autoskillit.pipeline.pr_gates import (
    is_ci_passing,
    is_review_passing,
    partition_prs,
)
from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter
from autoskillit.pipeline.timings import DefaultTimingLog, TimingEntry
from autoskillit.pipeline.tokens import DefaultTokenLog, TokenEntry

__all__ = [
    # branch_guard
    "is_protected_branch",
    # audit
    "DefaultAuditLog",
    "FailureRecord",
    "STDERR_MAX_LEN",
    "COMMAND_MAX_LEN",
    # mcp_response
    "DefaultMcpResponseLog",
    "McpResponseEntry",
    # timings
    "DefaultTimingLog",
    "TimingEntry",
    # tokens
    "DefaultTokenLog",
    "TokenEntry",
    # gate
    "DefaultGateState",
    "GATED_TOOLS",
    "UNGATED_TOOLS",
    "gate_error_result",
    "headless_error_result",
    # telemetry_fmt
    "TelemetryFormatter",
    # background
    "DefaultBackgroundSupervisor",
    "write_status",
    # context
    "ToolContext",
    # pr_gates
    "is_ci_passing",
    "is_review_passing",
    "partition_prs",
]
