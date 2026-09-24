"""Shared backend-compatibility setup for direct headless executor callers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    SKILL_CAPABILITY_REGISTRY,
    CodingAgentBackend,
    SemanticAdaptationContext,
    SkillContractError,
    SkillExecutionRole,
    SkillResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
    extract_skill_name,
    render_target_skill_command,
)
from autoskillit.server.tools._preflight import (
    _get_fix_required_hook_matchers,
    check_session_invariant_semantic_feasibility,
    check_skill_semantic_feasibility,
)
from autoskillit.workspace import (
    SkillProjectionBinding,
    SkillProjectionContext,
    build_skill_projection_binding,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


@dataclass(frozen=True, slots=True)
class DirectSkillDispatch:
    """Projected materialization retained for one direct headless dispatch."""

    add_dirs: tuple[ValidatedAddDir, ...]
    session_id: str
    capability_contract: SkillProjectionBinding
    resolved_command: str
    invocation: object
    projection_context: SkillProjectionContext

    def __post_init__(self) -> None:
        if not self.resolved_command:
            raise SkillContractError("direct skill dispatch must bind an invocation")

    def cleanup(self, tool_ctx: ToolContext) -> None:
        if tool_ctx.session_skill_manager is not None:
            tool_ctx.session_skill_manager.cleanup_session(self.session_id)


def _candidate_backend_rejection_reason(
    *,
    skill_info: object | None,
    effective_backend_obj: CodingAgentBackend,
    parent_sandbox_mode: str,
    write_spec: WriteBehaviorSpec | None,
    binary_available: bool,
) -> str | None:
    """Return the first reason a fresh candidate cannot launch that no launch evidence can lift.

    Candidate selection evaluates the whole resolved closure against the exact
    worker backend: closure capabilities, a backend-absolute root semantic refusal,
    sandbox, then binary. The caller evaluates evidence-dependent semantic admission
    after managed-join issuance. Direct callers retain the root-only gate below.
    """
    closure = tuple(getattr(skill_info, "closure", ()))
    capabilities = set(getattr(skill_info, "capability_union", ()))
    capabilities.update(
        capability for member in closure for capability in getattr(member, "uses_capabilities", ())
    )
    if not effective_backend_obj.capabilities.anthropic_provider_capable:
        not_applicable = sorted(
            capability
            for capability in capabilities
            if (capability_def := SKILL_CAPABILITY_REGISTRY.get(capability)) is not None
            and capability_def.codex_status == "not-applicable"
        )
        if not_applicable:
            return (
                f"backend {effective_backend_obj.name!r} cannot run skill closure with "
                f"not-applicable capabilities {not_applicable!r}"
            )

    root = getattr(skill_info, "root", None)
    semantic_error = check_session_invariant_semantic_feasibility(
        getattr(root, "semantic_plan", None),
        effective_backend_obj,
    )
    if semantic_error:
        return semantic_error

    requires_workspace_write = write_spec is not None and (
        write_spec.mode is not None or write_spec.external_effect != "none"
    )
    requires_workspace_write = requires_workspace_write or any(
        override.startswith("sandbox_workspace_write.")
        for capability in capabilities
        if (capability_def := SKILL_CAPABILITY_REGISTRY.get(capability)) is not None
        for override in capability_def.required_sandbox_overrides
    )
    if parent_sandbox_mode != "workspace-write" and requires_workspace_write:
        return (
            "candidate requires a workspace-write sandbox for its declared "
            "write behavior or capability overrides"
        )

    if not binary_available:
        binary = effective_backend_obj.capabilities.process_name
        return (
            f"candidate backend {effective_backend_obj.name!r} requires binary {binary!r} on PATH"
        )
    return None


def _check_backend_compat(
    skill_command: str,
    resolved_command: str,
    effective_order_id: str,
    target_name: str | None,
    skill_info: object | None,
    effective_backend_obj: CodingAgentBackend | None,
    skill_resolver: object | None,
    adaptation_context: SemanticAdaptationContext | None = None,
) -> str | None:
    """Fail closed when an effective skill invocation is backend-incompatible.

    ``skill_info`` retains its compatibility-facing parameter name because
    ``tools_execution`` still calls this helper by keyword. The value is an
    ``EffectiveSkillInvocation``; policy reads its closure-wide capability union
    while semantic target admission is intentionally root-only.
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
    root = getattr(skill_info, "root", None)
    root_plan = getattr(root, "semantic_plan", None)
    semantic_error = check_skill_semantic_feasibility(
        root_plan,
        effective_backend_obj,
        adaptation_context=adaptation_context,
    )
    if semantic_error:
        return SkillResult.infeasible(
            skill_name=target_name,
            backend=effective_backend,
            diagnostic=semantic_error,
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
                visibility=tool_ctx.config.skill_visibility_spec(),
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
    *,
    adaptation_context: SemanticAdaptationContext | None = None,
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
            visibility=tool_ctx.config.skill_visibility_spec(),
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
        adaptation_context=adaptation_context,
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
        adaptation_context=adaptation_context,
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
        invocation.root.source_ref or invocation.root.source,
        backend.conventions if backend is not None else None,
    )
    capability_contract = build_skill_projection_binding(
        projection_context,
        artifact_paths=(add_dir.path,),
    )
    return DirectSkillDispatch(
        add_dirs=(add_dir,),
        session_id=session_id,
        capability_contract=capability_contract,
        resolved_command=resolved_command,
        invocation=invocation,
        projection_context=projection_context,
    ), None
