"""Shared backend-compatibility setup for direct headless executor callers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    CodingAgentBackend,
    SkillResult,
    extract_skill_name,
    resolve_target_skill,
)
from autoskillit.server.tools._preflight import (
    _get_fix_required_hook_matchers,
    check_hard_capability_feasibility,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


def _is_backend_incompatible(skill_info: object, effective_backend: str) -> bool:
    """Return True if skill's backend_requirements exclude effective_backend."""
    reqs = getattr(skill_info, "backend_requirements", None)
    return bool(reqs and effective_backend not in reqs)


def _check_backend_compat(
    skill_command: str,
    resolved_command: str,
    effective_order_id: str,
    target_name: str | None,
    skill_info: object | None,
    effective_backend_obj: CodingAgentBackend | None,
    skill_resolver: object | None,
) -> str | None:
    """Fail closed when a skill is incompatible with its effective backend."""
    if target_name is None:
        return None
    if skill_resolver is None:
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Cannot verify backend compatibility for skill {target_name!r}: "
                "skill resolver is not available."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    if effective_backend_obj is None:
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Cannot dispatch skill {target_name!r}: session backend is not configured."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    if skill_info is None:
        return None
    effective_backend = effective_backend_obj.name
    if _is_backend_incompatible(skill_info, effective_backend):
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Skill {target_name!r} requires backend "
                f"{sorted(getattr(skill_info, 'backend_requirements', []))} but session "
                f"backend is {effective_backend!r}."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    skill_capabilities: frozenset[str] = getattr(skill_info, "uses_capabilities", frozenset())
    if skill_capabilities:
        hard_capability_error = check_hard_capability_feasibility(
            skill_capabilities, effective_backend_obj
        )
        if hard_capability_error:
            return SkillResult.crashed(
                exception=RuntimeError(
                    f"Skill {target_name!r} is not feasible on backend "
                    f"{effective_backend!r}: {hard_capability_error}"
                ),
                skill_command=resolved_command,
                order_id=effective_order_id,
            ).to_json()
    fix_required_matchers = _get_fix_required_hook_matchers(
        effective_backend_obj.capabilities.applicable_guards,
    )
    if fix_required_matchers:
        return SkillResult.crashed(
            exception=RuntimeError(
                f"Cannot dispatch skill {target_name!r} on backend "
                f"{effective_backend!r}: HOOK_REGISTRY contains fix-required "
                f"entries [{', '.join(fix_required_matchers)}] that cannot be "
                f"enforced by this backend."
            ),
            skill_command=resolved_command,
            order_id=effective_order_id,
        ).to_json()
    return None


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
