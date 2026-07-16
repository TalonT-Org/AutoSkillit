"""Shared backend-compatibility setup for direct headless executor callers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from autoskillit.core import DISPATCH_ID_ENV_VAR, extract_skill_name, resolve_target_skill
from autoskillit.server.tools.tools_execution import _check_backend_compat

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


def _resolve_and_check_backend_compat(
    skill_command: str,
    tool_ctx: ToolContext,
) -> str | None:
    """Resolve a direct skill invocation and run the fail-closed compatibility gate."""
    resolved_command = skill_command
    target_name = extract_skill_name(skill_command)
    skill_info: object | None = None
    if tool_ctx.skill_resolver is not None:
        resolved_command, target_name = resolve_target_skill(
            skill_command,
            tool_ctx.skill_resolver,
        )
        if target_name is not None:
            skill_info = tool_ctx.skill_resolver.resolve(target_name)

    return _check_backend_compat(
        skill_command=skill_command,
        resolved_command=resolved_command,
        effective_order_id=os.environ.get(DISPATCH_ID_ENV_VAR, ""),
        target_name=target_name,
        skill_info=skill_info,
        effective_backend_obj=tool_ctx.backend,
        skill_resolver=tool_ctx.skill_resolver,
    )
