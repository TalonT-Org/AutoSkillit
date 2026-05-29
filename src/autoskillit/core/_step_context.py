"""ContextVars for current pipeline step attribution.

IL-0 module — stdlib only.  Read by execution/ and server/ layers at
GitHub API recording time; set/reset by tools_execution.run_skill().
"""

from __future__ import annotations

from contextvars import ContextVar

current_step_name: ContextVar[str] = ContextVar("current_step_name", default="")
current_order_id: ContextVar[str] = ContextVar("current_order_id", default="")
