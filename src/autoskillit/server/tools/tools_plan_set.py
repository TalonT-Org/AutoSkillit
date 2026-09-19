"""Headless plan-set authority binding tool."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetRejectReason,
    get_logger,
    resolve_temp_dir,
)
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _as_bool(value: str) -> bool:
    if value.strip().lower() in {"true", "1", "yes"}:
        return True
    if value.strip().lower() in {"false", "0", "no"}:
        return False
    raise ValueError("seal must be true or false")


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"}, annotations={"readOnlyHint": True}
)
@_cancellation_shield()
@track_response_size("bind_plan_set")
async def bind_plan_set(
    plan_parts: str,
    cwd: str,
    issue_url: str = "",
    parent_authority_path: str = "",
    seal: str = "true",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Bind plan parts to one verified authority artifact. Never raises."""
    try:
        from autoskillit.server import _get_ctx  # circular-break: tool registration owns the app
        from autoskillit.server.recipe._recipe_execution import (  # circular-break
            get_recipe_execution,
        )

        tool_ctx = _get_ctx()
        if not os.path.isdir(cwd):
            return json.dumps(
                {
                    "success": False,
                    "error": f"cwd does not exist: {cwd}",
                    "reason": PlanSetRejectReason.CONTAINMENT.value,
                }
            )
        materializer = tool_ctx.plan_set_materializer
        if materializer is None:
            return json.dumps(
                {
                    "success": False,
                    "error": "plan-set materializer is unavailable",
                    "reason": PlanSetRejectReason.INTERNAL.value,
                }
            )
        installed = get_recipe_execution(tool_ctx)
        execution_generation = installed.snapshot.execution_id if installed is not None else ""
        request = PlanSetBindRequest(
            plan_parts_raw=plan_parts,
            allowed_root=resolve_temp_dir(Path(cwd), tool_ctx.config.workspace.temp_dir),
            issue_url=issue_url,
            parent_authority_path=parent_authority_path,
            seal=_as_bool(seal),
            step_name=step_name,
            execution_generation=execution_generation,
            kitchen_id=tool_ctx.kitchen_id,
            dispatch_id=os.environ.get(DISPATCH_ID_ENV_VAR, ""),
            binding_mode=(
                PlanSetBindingMode.RECIPE
                if installed is not None
                else PlanSetBindingMode.STANDALONE
            ),
        )
        return json.dumps((await materializer.bind(request)).to_dict())
    except Exception as exc:
        logger.error("bind_plan_set failed", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "reason": PlanSetRejectReason.INTERNAL.value,
            }
        )
