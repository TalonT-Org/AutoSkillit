"""Shared backend-compatibility setup for direct headless executor callers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    CodingAgentBackend,
    SkillContractError,
    SkillExecutionRole,
    SkillResult,
    ValidatedAddDir,
    extract_skill_name,
    render_target_skill_command,
)
from autoskillit.server.tools._preflight import (
    _get_fix_required_hook_matchers,
    check_hard_capability_feasibility,
)
from autoskillit.workspace import (
    EffectiveSkillDispatchContract,
    SkillProjectionContext,
    build_effective_skill_dispatch_contract,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


@dataclass(frozen=True, slots=True)
class DirectSkillDispatch:
    """Projected materialization retained for one direct headless dispatch."""

    add_dirs: tuple[ValidatedAddDir, ...]
    session_id: str
    resolved_command: str
    invocation: object
    projection_context: SkillProjectionContext
    capability_contract: EffectiveSkillDispatchContract

    def cleanup(self, tool_ctx: ToolContext) -> None:
        if tool_ctx.session_skill_manager is not None:
            tool_ctx.session_skill_manager.cleanup_session(self.session_id)


def _is_backend_incompatible(skill_invocation: object, effective_backend: str) -> bool:
    """Return True if the closure's derived requirements exclude the backend."""
    reqs = getattr(skill_invocation, "backend_requirements", None)
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
    """Fail closed when an effective skill invocation is backend-incompatible.

    ``skill_info`` retains its compatibility-facing parameter name because
    ``tools_execution`` still calls this helper by keyword. The value is an
    ``EffectiveSkillInvocation``; policy reads its closure-wide capability union
    and derived backend requirements.
    """
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
    skill_capabilities: frozenset[str] = getattr(skill_info, "capability_union", frozenset())
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
    skill_invocation: object | None = None
    if tool_ctx.skill_resolver is not None and target_name is not None:
        try:
            skill_invocation = tool_ctx.skill_resolver.resolve_invocation(
                target_name,
                tool_ctx.project_dir,
                SkillExecutionRole.SESSION,
                config=tool_ctx.config,
                recipe_packs=tool_ctx.active_recipe_packs,
                recipe_features=tool_ctx.active_recipe_features,
            )
        except SkillContractError as exc:
            return SkillResult.crashed(
                exception=exc,
                skill_command=resolved_command,
                order_id=os.environ.get(DISPATCH_ID_ENV_VAR, ""),
            ).to_json()

    return _check_backend_compat(
        skill_command=skill_command,
        resolved_command=resolved_command,
        effective_order_id=os.environ.get(DISPATCH_ID_ENV_VAR, ""),
        target_name=target_name,
        skill_info=skill_invocation,
        effective_backend_obj=tool_ctx.backend,
        skill_resolver=tool_ctx.skill_resolver,
    )


def _prepare_direct_skill_dispatch(
    skill_command: str,
    cwd: str | Path,
    tool_ctx: ToolContext,
) -> tuple[DirectSkillDispatch | None, str | None]:
    """Resolve policy once, then materialize its agent-safe projection."""
    target_name = extract_skill_name(skill_command)
    order_id = os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if target_name is None:
        return None, SkillResult.crashed(
            exception=SkillContractError("Direct dispatch requires a skill command"),
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    if tool_ctx.skill_resolver is None:
        return None, SkillResult.crashed(
            exception=SkillContractError(
                f"Cannot resolve direct skill {target_name!r}: skill resolver is unavailable"
            ),
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    if tool_ctx.session_skill_manager is None:
        return None, SkillResult.crashed(
            exception=SkillContractError(
                f"Cannot materialize direct skill {target_name!r}: "
                "session skill manager is unavailable"
            ),
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    try:
        invocation = tool_ctx.skill_resolver.resolve_invocation(
            target_name,
            tool_ctx.project_dir,
            SkillExecutionRole.SESSION,
            config=tool_ctx.config,
            recipe_packs=tool_ctx.active_recipe_packs,
            recipe_features=tool_ctx.active_recipe_features,
        )
    except SkillContractError as exc:
        return None, SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()

    compatibility_error = _check_backend_compat(
        skill_command=skill_command,
        resolved_command=skill_command,
        effective_order_id=order_id,
        target_name=target_name,
        skill_info=invocation,
        effective_backend_obj=tool_ctx.backend,
        skill_resolver=tool_ctx.skill_resolver,
    )
    if compatibility_error is not None:
        return None, compatibility_error

    normalized_cwd = Path(cwd).resolve()
    backend = tool_ctx.backend
    projection_context = SkillProjectionContext(
        cwd=normalized_cwd,
        invocation=invocation,
        backend=backend,
        conventions=backend.conventions if backend is not None else None,
        substitutions={"{{AUTOSKILLIT_TEMP}}": str(normalized_cwd / ".autoskillit" / "temp")},
        gating=False,
    )
    session_id = f"direct-{uuid4().hex[:12]}"
    try:
        add_dir = tool_ctx.session_skill_manager.materialize_invocation(
            session_id,
            invocation,
            projection_context,
        )
    except (OSError, RuntimeError, ValueError, SkillContractError) as exc:
        tool_ctx.session_skill_manager.cleanup_session(session_id)
        return None, SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    resolved_command = render_target_skill_command(
        skill_command,
        invocation.root.source_ref,
        backend.conventions if backend is not None else None,
    )
    capability_contract = build_effective_skill_dispatch_contract(
        resolved_command,
        projection_context,
        artifact_paths=(add_dir.path,),
    )
    return DirectSkillDispatch(
        add_dirs=(add_dir,),
        session_id=session_id,
        resolved_command=resolved_command,
        invocation=invocation,
        projection_context=projection_context,
        capability_contract=capability_contract,
    ), None
