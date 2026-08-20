"""MCP tool handler: run_python."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import (
    _check_recipe_read_prohibition,
    _require_enabled,
    _require_orchestrator_or_higher,
)
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_segment_delivery import attach_recipe_segment
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import (
    maybe_promote_work_dir,
    resolve_relative_path_args,
    server_injected_run_python_args,
    validate_path_arg_anchoring,
)

if TYPE_CHECKING:
    from autoskillit.server._recipe_segment_delivery import PreparedRecipeSegmentDelivery

logger = get_logger(__name__)


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_python")
@track_response_size("run_python")
async def run_python(
    callable: str,
    args: dict[str, object] | None = None,
    timeout: int = 30,
    work_dir: str = "",
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Call a Python function directly by dotted module path.

    Imports the module, resolves the function, and calls it with the
    provided arguments. Use for lightweight decision logic that does
    not need an LLM session (counter checks, status lookups, eligibility
    decisions).

    Both sync and async functions are supported. Async functions are
    awaited directly; sync functions run in a thread pool.

    Args:
        callable: Dotted path to the function (e.g. "mypackage.module.function").
        args: Keyword arguments to pass to the function.
        timeout: Max seconds before aborting the call (default 30).
        work_dir: When set, relative path-like args (output_dir, etc.) are
            anchored to this directory before the callable is invoked.
        step_name: Optional YAML step key for progressive recipe delivery.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_python")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if (gate := _check_recipe_read_prohibition(callable_name=callable)) is not None:
        return gate
    try:
        prepared_segment: PreparedRecipeSegmentDelivery | None = None
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        prepared_segment = _te_pkg.prepare_recipe_segment_delivery(tool_ctx, step_name)
        with structlog.contextvars.bound_contextvars(tool="run_python"):
            logger.info("run_python", callable=callable, timeout=timeout)
            await _te_pkg._notify(
                ctx,
                "info",
                f"run_python: {callable}",
                "autoskillit.run_python",
                extra={"callable": callable},
            )
            anchor_err = validate_path_arg_anchoring(args, work_dir)
            if anchor_err:
                return json.dumps(
                    attach_recipe_segment(
                        {"success": False, "error": anchor_err},
                        prepared_segment,
                        success=False,
                    )
                )
            promoted = maybe_promote_work_dir(args, work_dir)
            if promoted != work_dir:
                logger.warning(
                    "run_python auto-promoted work_dir from args to tool level",
                    callable=callable,
                    work_dir=promoted,
                )
                work_dir = promoted
            if work_dir and not Path(work_dir).is_absolute():
                return json.dumps(
                    attach_recipe_segment(
                        {
                            "success": False,
                            "error": f"run_python: work_dir must be absolute, got {work_dir!r}",
                        },
                        prepared_segment,
                        success=False,
                    )
                )
            resolved_args = args
            if work_dir:
                resolved_args = resolve_relative_path_args(args or {}, work_dir)
            result = await _te_pkg._import_and_call(
                callable,
                args=resolved_args,
                timeout=float(timeout),
                server_injected_args=server_injected_run_python_args(callable, tool_ctx),
            )
            if not result.get("success"):
                await _te_pkg._notify(
                    ctx,
                    "error",
                    "run_python failed",
                    "autoskillit.run_python",
                    extra={"callable": callable},
                )
            rendered = _te_pkg.shape_execution_response(
                tool_ctx,
                result,
                tool_name="run_python",
                work_dir=work_dir,
            )
            shaped = json.loads(rendered)
            if not isinstance(shaped, dict):
                raise TypeError("run_python response must be a JSON object")
            return json.dumps(
                attach_recipe_segment(
                    shaped,
                    prepared_segment,
                    success=shaped.get("success") is True,
                )
            )
    except Exception as exc:
        logger.error("run_python unhandled exception", exc_info=True)
        return json.dumps(
            attach_recipe_segment(
                {"success": False, "error": f"{type(exc).__name__}: {exc}"},
                prepared_segment,
                success=False,
            )
        )
