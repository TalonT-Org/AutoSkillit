"""Dispatch metadata and projection context resolvers."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    SKILL_CAPABILITY_REGISTRY,
    WORKTREE_SKILLS,
    BackendPinResolution,
    CodingAgentBackend,
    EffectiveSkillInvocationAuthority,
    ExplorationVectorApplicabilityId,
    RepositoryProfileId,
    SkillContractError,
    ValidatedAddDir,
    WriteBehaviorSpec,
    extract_skill_name,
    is_git_worktree,
)
from autoskillit.execution import SkillSessionContract
from autoskillit.pipeline import canonical_step_name
from autoskillit.recipe import (
    AuditOutputMode,
    SkillContract,
    select_audit_output_contract,
)
from autoskillit.server._misc import SkillProjectionContext
from autoskillit.server.tools._execution_helpers._skill_contract import (
    deserialize_skill_contract,
)
from autoskillit.server.tools._types import deny_envelope
from autoskillit.workspace import (
    SkillProjectionBinding,
    build_skill_projection_binding,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


def check_review_approach_plan_path(step_name: str, skill_command: str) -> str | None:
    """Reject review-approach issue URLs where a plan path is required."""
    if canonical_step_name(step_name) != "review_approach":
        return None
    parts = skill_command.split()
    if len(parts) < 2:
        return None
    first_arg = parts[1]
    if not first_arg.startswith(("https://", "http://")):
        return None
    return json.dumps(
        deny_envelope(
            (
                "review_approach requires a plan file path argument (a path "
                "under the project's temp directory produced by "
                "rectify/make_plan), not an issue URL."
            ),
            stage="preflight:plan_path",
            retriable=False,
        )
    )


def derive_run_cmd_write_prefixes() -> tuple[str, ...]:
    """Read allowed write prefixes from the canonical environment variables."""
    multi = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "")
    if multi:
        return tuple(p for p in multi.split(":") if p)
    single = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
    return (single,) if single else ()


def compute_write_prefixes(
    write_watch_dirs: list[Path],
    cwd: str,
    skill_command: str,
) -> tuple[str, tuple[str, ...]]:
    worktree_write_prefixes: list[str] = []
    extracted = extract_skill_name(skill_command)
    if write_watch_dirs and extracted and extracted in WORKTREE_SKILLS:
        resolved_cwd = Path(cwd).resolve()
        if is_git_worktree(resolved_cwd):
            worktree_write_prefixes.extend(
                (str(resolved_cwd) + "/", str(resolved_cwd.parent) + "/")
            )
        else:
            nested_wt = resolved_cwd / "worktrees"
            sibling_wt = resolved_cwd.parent / "worktrees"
            if nested_wt.is_dir():
                worktree_write_prefixes.append(str(nested_wt) + "/")
            if sibling_wt.is_dir() or not nested_wt.is_dir():
                worktree_write_prefixes.append(str(sibling_wt) + "/")
    base_prefixes = [str(d.resolve()) + "/" for d in write_watch_dirs]
    return (
        base_prefixes[0] if base_prefixes else "",
        tuple(base_prefixes + worktree_write_prefixes),
    )


def scope_covers_cwd(allowed_write_prefixes: tuple[str, ...], cwd: str) -> bool:
    """Return whether any allowed prefix lexically covers cwd."""
    if not allowed_write_prefixes or not cwd:
        return False
    resolved_cwd = str(Path(cwd).resolve()).rstrip("/") + "/"
    return any(resolved_cwd.startswith(prefix) for prefix in allowed_write_prefixes)


def invocation_member_names(
    invocation: EffectiveSkillInvocationAuthority,
) -> frozenset[str]:
    """Return the exact member inventory bound to an effective invocation."""
    return frozenset(member.name for member in invocation.closure)


def build_fresh_projection_context(
    cwd: str,
    invocation: EffectiveSkillInvocationAuthority,
) -> SkillProjectionContext:
    """Bind a fresh invocation to normalized backend-neutral projection authority."""
    normalized_cwd = Path(cwd).resolve()
    return SkillProjectionContext(
        cwd=normalized_cwd,
        invocation=invocation,
        substitutions={"{{AUTOSKILLIT_TEMP}}": str(normalized_cwd / ".autoskillit" / "temp")},
        gating=False,
    )


def bind_projection_backend(
    context: SkillProjectionContext,
    backend: CodingAgentBackend | None,
    *,
    resolution: BackendPinResolution | None = None,
    parent_sandbox_mode: str = "workspace-write",
    resolved_exploration_profile: RepositoryProfileId | None = None,
    active_exploration_applicabilities: frozenset[ExplorationVectorApplicabilityId] | None = None,
) -> SkillProjectionContext:
    """Complete fresh projection authority after capability-driven backend selection."""
    if resolution is not None and (backend is None or backend.name != resolution.backend):
        effective = None if backend is None else backend.name
        raise SkillContractError(
            "projection backend disagrees with resolved backend authority: "
            f"resolved={resolution.backend!r}, effective={effective!r}, "
            f"tier={resolution.tier!r}, key_path={resolution.key_path!r}"
        )
    if context.backend is not None and context.backend.name != (backend.name if backend else None):
        raise SkillContractError("projection context cannot be rebound to a different backend")
    launch_context_ref = (
        f"skill:{context.invocation.root.name}"
        if context.invocation is not None and context.exploration_vectors
        else None
    )
    return dataclasses.replace(
        context,
        backend=backend,
        conventions=backend.conventions if backend is not None else None,
        exploration_launch_context_ref=launch_context_ref,
        resolved_exploration_profile=resolved_exploration_profile,
        active_exploration_applicabilities=(
            active_exploration_applicabilities
            if active_exploration_applicabilities is not None
            else context.active_exploration_applicabilities
        ),
        parent_sandbox_mode=parent_sandbox_mode,
    )


def build_validated_skill_dispatch_contract(
    projection_context: SkillProjectionContext,
    add_dirs: list[ValidatedAddDir],
    stored_contract: SkillSessionContract | None,
) -> SkillProjectionBinding:
    """Build immutable executor authority and verify resumed projected bytes."""
    contract = build_skill_projection_binding(
        projection_context,
        artifact_paths=(add_dir.path for add_dir in add_dirs),
    )
    if stored_contract is not None and dict(contract.projected_digests) != dict(
        stored_contract.projected_digests
    ):
        raise SkillContractError("resumed projected artifacts do not match the persisted contract")
    return contract


def aggregate_sandbox_overrides(skill_caps: frozenset[str]) -> frozenset[str]:
    """Aggregate required sandbox overrides from declared capabilities."""
    return frozenset().union(
        *(
            SKILL_CAPABILITY_REGISTRY[cap].required_sandbox_overrides
            for cap in skill_caps
            if cap in SKILL_CAPABILITY_REGISTRY
        )
    )


def resolve_skill_dispatch_metadata(
    tool_ctx: ToolContext,
    skill_command: str,
    stored_contract: SkillSessionContract | None,
    *,
    audit_output_mode: AuditOutputMode | None = None,
) -> tuple[list[str], WriteBehaviorSpec | None, SkillContract | None]:
    """Resolve fresh metadata or restore the exact persisted execution metadata."""
    if stored_contract is not None:
        return (
            list(stored_contract.expected_output_patterns),
            stored_contract.write_behavior,
            deserialize_skill_contract(stored_contract.skill_contract_json),
        )
    resolver = tool_ctx.skill_contract_resolver
    contract = resolver(skill_command) if resolver else None
    pattern_resolver = tool_ctx.output_pattern_resolver
    patterns = list(pattern_resolver(skill_command)) if pattern_resolver else []
    if contract is not None and audit_output_mode is not None:
        contract = select_audit_output_contract(contract, audit_output_mode)
        patterns = list(contract.expected_output_patterns)
    write_resolver = tool_ctx.write_expected_resolver
    write_spec = write_resolver(skill_command) if write_resolver else None
    return patterns, write_spec, contract


def resolve_step_name_from_recipe(
    skill_command: str,
    active_recipe_steps: dict[str, object],
) -> tuple[str, bool]:
    """Match a command prefix to exactly one active recipe step."""
    command_prefix = skill_command.split()[0] if skill_command.strip() else ""
    if not command_prefix:
        return ("", False)
    matches = [
        step_name
        for step_name, step in active_recipe_steps.items()
        if isinstance((with_args := getattr(step, "with_args", None)), dict)
        and (step_command := with_args.get("skill_command", ""))
        and step_command.split()[0] == command_prefix
    ]
    if len(matches) == 1:
        return (matches[0], False)
    return ("", len(matches) > 1)
