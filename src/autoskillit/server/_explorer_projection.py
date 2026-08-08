"""Server-owned explorer projection and launch identity helpers."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import (
    BUNDLED_EXPLORER_ROLES,
    CODEX_EFFORT_MAPPING,
    BackendPinResolution,
    ChildExecutionIdentity,
    CodingAgentBackend,
    EffectiveSkillInvocationAuthority,
    ExecutionIdentity,
    ExplorationContextStoreProtocol,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDisposition,
    RepositoryProfileId,
    SkillContractError,
    ValidatedAddDir,
    agent_definition_digest,
    get_logger,
    load_bundled_agent_definitions,
    strip_context_window_suffix,
)
from autoskillit.core import (
    session_type as _resolve_session_type,
)
from autoskillit.exploration import resolve_repository_profile
from autoskillit.pipeline.exploration_context import is_explorer_binding_eligible
from autoskillit.server._misc import SkillProjectionContext

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)


def _has_exact_identity_field(content: str, field: str, value: str) -> bool:
    expected = f"{field}: {value}"
    return any(line.strip() == expected for line in content.splitlines())


def _extract_identity_field(content: str, field: str) -> str | None:
    prefix = f"{field}: "
    for line in content.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()[len(prefix) :]
    return None


def _explorer_launch_identity(
    invocation: EffectiveSkillInvocationAuthority | None,
) -> tuple[Path, str] | None:
    """Return only pre-override, registry-derived parent launch identity."""
    if invocation is None:
        return None
    source_ref = invocation.root.source_ref
    project_root = invocation.project_root
    if source_ref is None or project_root is None:
        raise SkillContractError("Explorer invocation lacks trusted source identity")
    origin = getattr(source_ref.origin, "value", str(source_ref.origin))
    return Path(project_root).resolve(), f"{origin}:{source_ref.skill_path}"


def _resolve_exploration_profile(
    tool_ctx: ToolContext,
    projection_context: SkillProjectionContext,
    *,
    active_applicabilities: frozenset[ExplorationVectorApplicabilityId],
) -> RepositoryProfileId | None:
    """Resolve active migrated profile:auto from the trusted invocation root."""
    vectors = tuple(
        vector for members in projection_context.exploration_vectors.values() for vector in members
    )
    if not any(
        vector.disposition is ExplorationVectorDisposition.MIGRATED
        and vector.applicability in active_applicabilities
        and vector.profile is RepositoryProfileId.AUTO
        for vector in vectors
    ):
        return None
    store = tool_ctx.exploration_context_store
    project_root = projection_context.project_root
    if store is None or project_root is None:
        raise SkillContractError("profile:auto requires a trusted exploration context")
    trusted_root = store.trusted_root.resolve()
    if project_root.resolve() != trusted_root:
        raise SkillContractError("profile:auto project root is not the trusted repository root")
    return resolve_repository_profile(trusted_root)


def _resolve_exploration_applicabilities(
    projection_context: SkillProjectionContext,
    *,
    skill_inputs: dict[str, str | int | bool] | None,
    output_dir: str,
) -> frozenset[ExplorationVectorApplicabilityId]:
    """Evaluate the closed Phase-C branch predicates from attested recipe inputs."""
    active = {ExplorationVectorApplicabilityId.ALWAYS}
    vectors = tuple(
        vector for members in projection_context.exploration_vectors.values() for vector in members
    )
    if not any(
        vector.disposition is ExplorationVectorDisposition.MIGRATED
        and vector.applicability is ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP
        for vector in vectors
    ):
        return frozenset(active)
    analysis_value = (skill_inputs or {}).get("analysis_path")
    if not isinstance(analysis_value, str) or not analysis_value or not output_dir:
        raise SkillContractError(
            "planner extract-domain applicability requires analysis_path and output_dir"
        )
    analysis_path = Path(analysis_value).resolve()
    output_root = Path(output_dir).resolve()
    if output_root not in analysis_path.parents or not analysis_path.is_file():
        raise SkillContractError("analysis_path is outside the server-owned planner output")
    if analysis_path.stat().st_size > 1_000_000:
        raise SkillContractError("analysis_path exceeds the applicability input bound")
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillContractError("analysis_path is not valid bounded JSON") from exc
    if not isinstance(analysis, dict):
        raise SkillContractError("analysis_path must contain a JSON object")
    module_count = analysis.get("module_count")
    architecture_style = analysis.get("architecture_style")
    if type(module_count) is not int or not isinstance(architecture_style, str):
        raise SkillContractError(
            "analysis_path lacks typed module_count and architecture_style applicability fields"
        )
    if module_count > 20 or architecture_style.casefold() in {"layered", "hexagonal"}:
        active.add(ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP)
    return frozenset(active)


def _issue_explorer_binding_env(
    tool_ctx: ToolContext,
    *,
    session_id: str,
    projection_context: SkillProjectionContext,
    identity: tuple[Path, str] | None,
    authority_home: Path,
) -> dict[str, dict[str, str]] | None:
    """Mint one shared principal replicated to both terminal role projections."""
    backend = projection_context.backend
    if identity is None or backend is None:
        return None
    if not is_explorer_binding_eligible(
        has_identity=True,
        has_backend=True,
        terminal_explorer_capable=backend.capabilities.terminal_explorer_capable,
        session_scoped_explorer_capable=backend.capabilities.session_scoped_explorer_capable,
        parent_sandbox_mode=projection_context.parent_sandbox_mode,
        session_type=_resolve_session_type(),
    ):
        return None
    # Session-scoped backends (Claude) are eligible but use a different authority
    # model — per-child env bindings structurally cannot apply.
    if not backend.capabilities.terminal_explorer_capable:
        return None
    store = tool_ctx.exploration_context_store
    if store is None:
        raise SkillContractError("Explorer context store is unavailable")
    repository_root, parent_source_identity = identity
    definitions = tuple(
        definition
        for definition in load_bundled_agent_definitions()
        if definition.name in BUNDLED_EXPLORER_ROLES
    )
    if {definition.name for definition in definitions} != BUNDLED_EXPLORER_ROLES:
        raise SkillContractError("Canonical explorer AgentDef registry is incomplete")
    bindings = store.bind_launches(
        owner_id=f"uid:{os.getuid()}",
        session_id=session_id,
        cwd=projection_context.cwd,
        repository_root=repository_root,
        source_identities={
            definition.name: (
                f"{definition.name}:{agent_definition_digest(definition)}:{parent_source_identity}"
            )
            for definition in definitions
        },
        authority_home=authority_home,
    )
    return {role: dict(environment) for role, environment in bindings.items()}


def _cleanup_explorer_launch(
    store: ExplorationContextStoreProtocol[object],
    *,
    session_id: str,
    session_home: Path | None,
    backend: CodingAgentBackend | None,
) -> None:
    """Revoke durable exploration authority before attempting config scrubbing."""
    try:
        store.cleanup_session(session_id)
    except Exception:
        logger.warning(
            "exploration_context_cleanup_failed",
            session_id=session_id,
            exc_info=True,
        )
    finally:
        if backend is None or session_home is None:
            return
        try:
            backend.clear_explorer_binding_env(session_home, BUNDLED_EXPLORER_ROLES)
        except Exception:
            logger.warning(
                "exploration_binding_scrub_failed",
                session_id=session_id,
                exc_info=True,
            )


def _build_requested_execution_identity(
    *,
    projection_context: SkillProjectionContext | None,
    target_name: str | None,
    skill_add_dirs: Sequence[ValidatedAddDir],
    effective_backend: CodingAgentBackend | None,
    effective_model: str,
    explicit_resolution: BackendPinResolution | None,
) -> ExecutionIdentity:
    """Build deterministic requested parent and multi-child execution identity."""
    requested_children: tuple[ChildExecutionIdentity, ...] = ()
    if projection_context is not None and target_name:
        native_vectors = tuple(
            sorted(
                (
                    vector
                    for vector in projection_context.exploration_vectors.get(target_name, ())
                    if (
                        vector.disposition is ExplorationVectorDisposition.MIGRATED
                        and vector.role is not None
                        and vector.applicability
                        in projection_context.active_exploration_applicabilities
                    )
                ),
                key=lambda vector: vector.task.task_id,
            )
        )
        if native_vectors:
            definitions = {
                definition.name: definition for definition in load_bundled_agent_definitions()
            }
            missing_roles = {
                str(vector.role)
                for vector in native_vectors
                if vector.role is not None and vector.role not in definitions
            }
            if missing_roles:
                raise SkillContractError(
                    f"Native exploration roles are not registered: {sorted(missing_roles)!r}"
                )
            if not skill_add_dirs or projection_context.conventions is None:
                raise SkillContractError(
                    "Native exploration identity requires projected skill bytes"
                )
            if effective_backend is None:
                raise SkillContractError("Native exploration identity requires a bound backend")
            projected_skill_path = (
                Path(skill_add_dirs[0].path)
                / projection_context.conventions.skills_subdir
                / target_name
                / "SKILL.md"
            )
            projected_skill = projected_skill_path.read_text(encoding="utf-8")
            router_digests = set(
                re.findall(r"router_plan_digest: ([0-9a-f]{64})", projected_skill)
            )
            if len(router_digests) != 1:
                raise SkillContractError(
                    "Projected native exploration packets must bind one router-plan digest"
                )
            router_plan_digest = next(iter(router_digests))
            # Decode each native packet's JSON-embedded message argument once
            # and verify identity fields against the decoded prompt text.
            # The renderer embeds prompts via json.dumps(), so the projected
            # SKILL.md contains escaped newlines — exact-line matching against
            # the raw projected text cannot work for the Claude backend.
            dispatch_conventions = effective_backend.exploration_dispatch_renderer.conventions
            message_arg = dispatch_conventions.message_argument
            # Pattern: message_argument=<JSON string literal>
            # Anchored to the dispatch conventions' message argument name.
            message_pattern = re.compile(rf'{re.escape(message_arg)}=("(?:[^"\\]|\\.)*")')
            decoded_prompts: list[str] = []
            for match in message_pattern.finditer(projected_skill):
                try:
                    decoded = json.loads(match.group(1))
                except (json.JSONDecodeError, ValueError):
                    raise SkillContractError(
                        "Projected native exploration packet message is not valid JSON"
                    )
                if not isinstance(decoded, str):
                    raise SkillContractError(
                        "Projected native exploration packet message is not a string"
                    )
                decoded_prompts.append(decoded)
            requested_children = tuple(
                ChildExecutionIdentity(
                    task_id=vector.task.task_id,
                    role=str(vector.role),
                    plan_digest=router_plan_digest,
                    definition_digest=agent_definition_digest(definitions[str(vector.role)]),
                    requested_backend=(
                        effective_backend.name if effective_backend is not None else ""
                    ),
                    requested_model=definitions[str(vector.role)].codex.model or "",
                    requested_effort=(definitions[str(vector.role)].codex.reasoning_effort or ""),
                )
                for vector in native_vectors
            )
            if len(decoded_prompts) != len(requested_children):
                raise SkillContractError(
                    f"Expected {len(requested_children)} native exploration packets "
                    f"but found {len(decoded_prompts)} message arguments"
                )
            # Packets are embedded in SKILL.md marker/document order, which does not
            # generally match the task_id-sorted `requested_children` order, so pair
            # them by their declared task_id rather than by position.
            decoded_by_task_id: dict[str, str] = {}
            for decoded_prompt in decoded_prompts:
                packet_task_id = _extract_identity_field(decoded_prompt, "task_id")
                if packet_task_id is None:
                    raise SkillContractError(
                        "Projected native exploration packet message is missing a task_id field"
                    )
                if packet_task_id in decoded_by_task_id:
                    raise SkillContractError(
                        "Projected native exploration packet messages duplicate task_id "
                        f"{packet_task_id!r}"
                    )
                decoded_by_task_id[packet_task_id] = decoded_prompt
            if set(decoded_by_task_id) != {child.task_id for child in requested_children}:
                raise SkillContractError(
                    "Projected native exploration packet task ids do not match requested children"
                )
            for child in requested_children:
                decoded_prompt = decoded_by_task_id[child.task_id]
                if not _has_exact_identity_field(
                    decoded_prompt, "router_plan_digest", child.plan_digest
                ) or not _has_exact_identity_field(
                    decoded_prompt,
                    "role_definition_digest",
                    child.definition_digest,
                ):
                    raise SkillContractError(
                        "Projected native exploration packet identity is incomplete"
                    )
    requested_parent_backend = effective_backend.name if effective_backend is not None else ""
    return ExecutionIdentity(
        requested_parent_backend=requested_parent_backend,
        requested_parent_model=effective_model,
        requested_parent_effort=(
            CODEX_EFFORT_MAPPING.get(strip_context_window_suffix(effective_model), "")
            if effective_backend is not None
            and effective_backend.capabilities.terminal_explorer_capable
            else ""
        ),
        override_tier=explicit_resolution.tier if explicit_resolution is not None else "",
        override_key_path=(
            explicit_resolution.key_path if explicit_resolution is not None else ""
        ),
        children=requested_children,
    )


__all__ = [
    "_build_requested_execution_identity",
    "_cleanup_explorer_launch",
    "_explorer_launch_identity",
    "_issue_explorer_binding_env",
    "_resolve_exploration_applicabilities",
    "_resolve_exploration_profile",
]
