"""ContextVars for pipeline attribution and the current MCP operation.

IL-0 module — stdlib only.  Read by execution/ and server/ layers at
GitHub API recording time; set/reset by tools_execution.run_skill().
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..plugins._operation_lease import OperationLeaseHandle as _OperationLeaseHandle

__all__ = ["current_order_id", "current_step_name"]

current_step_name: ContextVar[str] = ContextVar("current_step_name", default="")
current_order_id: ContextVar[str] = ContextVar("current_order_id", default="")

_CURRENT_LEASE: ContextVar[_OperationLeaseHandle | None] = ContextVar(
    "autoskillit_current_operation_lease", default=None
)
