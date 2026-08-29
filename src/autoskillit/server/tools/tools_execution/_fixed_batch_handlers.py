"""MCP entry points for server-owned managed fixed-batch execution.

Native ``declare_join_batch`` remains a separate backend-capability path.  These
tools derive the managed parent, attestation, source identity, and result-read
scope from server-held request and session state before they touch the batch
supervisor or a durable result record.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from string import ascii_letters, digits
from typing import Any

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    SkillContractError,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    WriteBehaviorSpec,
    atomic_write,
    get_logger,
    render_target_skill_command,
)
from autoskillit.execution import (
    MANAGED_CODEX_LEAF_GUARD_SET,
    MANAGED_CODEX_PARENT_GUARD_SET,
)
from autoskillit.hooks import OUTCOME_COMPLETED, OUTCOME_FAILED
from autoskillit.hooks._hook_settings import validate_session_id
from autoskillit.hooks._session_binding import (
    SESSION_BINDING_SCHEMA_VERSION,
    LoadedSkillEntry,
    SessionBinding,
    SessionBindingError,
    binding_lock,
    normalize_skill_name,
    read_binding,
    resolve_binding_path,
    write_binding,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server._misc import project_agent_skill_document
from autoskillit.server._notify import track_response_size
from autoskillit.server._run_skill_completion import _request_session_identity
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import (
    bind_projection_backend,
    build_fresh_projection_context,
)
from autoskillit.server.tools.tools_execution._managed_fixed_batch import (
    ManagedFixedBatchLaunchBinding,
    ManagedLaunchBinding,
    ManagedLeafLaunchResult,
)
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    ManagedLeafPreparedLaunch,
    _ChildResourceOwnerRequest,
    _ChildWorktreeRequest,
    bind_managed_leaf,
    project_managed_leaf,
)

_MAX_ASSIGNMENTS = 128
_MAX_IDEMPOTENCY_KEY_CHARS = 160
_MAX_LABEL_CHARS = 160
_MAX_ROLE_CHARS = 100
_MAX_RUNTIME_KEY_CHARS = 240
_MAX_TASK_PROMPT_CHARS = 16_000
_MAX_RESULT_PAGE_BYTES = 8_192
_IDEMPOTENCY_KEY_CHARACTERS = ascii_letters + digits + "._-"

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _ManagedRequestFacts:
    launch: ManagedLaunchBinding
    binding: SessionBinding
    selected_source: LoadedSkillEntry
    channel_dir: Path
    adaptation_context: Any


def _deny(message: str) -> dict[str, object]:
    return {"success": False, "error": message}


def _text(
    value: object,
    *,
    field: str,
    maximum: int,
    required: bool = True,
    preserve_content: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SkillContractError(f"run_fixed_batch {field} must be text")
    candidate = value if preserve_content else " ".join(value.split())
    if required and not candidate.strip():
        raise SkillContractError(f"run_fixed_batch {field} must be non-empty")
    if len(candidate) > maximum:
        raise SkillContractError(f"run_fixed_batch {field} exceeds the {maximum}-character bound")
    return candidate


def _normalize_assignments(raw: object) -> tuple[ManagedLeafAssignmentInput, ...]:
    if not isinstance(raw, list) or not raw:
        raise SkillContractError("run_fixed_batch assignments must be a non-empty array")
    if len(raw) > _MAX_ASSIGNMENTS:
        raise SkillContractError(f"run_fixed_batch accepts at most {_MAX_ASSIGNMENTS} assignments")
    assignments: list[ManagedLeafAssignmentInput] = []
    for ordinal, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise SkillContractError(f"run_fixed_batch assignment {ordinal} must be an object")
        unknown = set(item) - {"role", "label", "task_prompt", "runtime_key"}
        if unknown:
            raise SkillContractError(
                f"run_fixed_batch assignment {ordinal} has unknown fields: {sorted(unknown)}"
            )
        assignments.append(
            ManagedLeafAssignmentInput(
                role=_text(item.get("role"), field="role", maximum=_MAX_ROLE_CHARS),
                label=_text(item.get("label"), field="label", maximum=_MAX_LABEL_CHARS),
                task_prompt=_text(
                    item.get("task_prompt"),
                    field="task_prompt",
                    maximum=_MAX_TASK_PROMPT_CHARS,
                    preserve_content=True,
                ),
                runtime_key=_text(
                    item.get("runtime_key", ""),
                    field="runtime_key",
                    maximum=_MAX_RUNTIME_KEY_CHARS,
                    required=False,
                ),
            )
        )
    return tuple(assignments)


def _validate_membership(
    assignments: Sequence[ManagedLeafAssignmentInput],
    selected_source: LoadedSkillEntry,
    adaptation: object,
) -> None:
    cardinality = selected_source.child_spawn_cardinality
    if not cardinality:
        raise SkillContractError("run_fixed_batch source has no declared child cardinality")
    received_roles = Counter(item.role for item in assignments)
    static_counts = {role: count for role, count in cardinality.items() if type(count) is int}
    if static_counts and any(
        received_roles.get(role, 0) != count for role, count in static_counts.items()
    ):
        raise SkillContractError(
            "run_fixed_batch assignments do not match declared role cardinality"
        )
    undeclared_roles = set(received_roles) - set(cardinality)
    if undeclared_roles:
        raise SkillContractError(
            f"run_fixed_batch assignments use undeclared roles: {sorted(undeclared_roles)}"
        )
    if all(type(count) is int for count in cardinality.values()) and len(assignments) != sum(
        int(count) for count in cardinality.values()
    ):
        raise SkillContractError(
            "run_fixed_batch assignments do not match declared static cardinality"
        )
    dynamic_roles = {role for role, count in cardinality.items() if isinstance(count, str)}
    if dynamic_roles:
        dynamic_keys = [item.runtime_key for item in assignments if item.role in dynamic_roles]
        if not dynamic_keys or any(not key for key in dynamic_keys):
            raise SkillContractError(
                "run_fixed_batch dynamic assignments require authoritative runtime keys"
            )
        if len(dynamic_keys) != len(set(dynamic_keys)):
            raise SkillContractError("run_fixed_batch dynamic runtime keys must be unique")
    logical_roles = getattr(adaptation, "logical_role_mapping", {})
    if logical_roles and any(role not in logical_roles for role in received_roles):
        raise SkillContractError(
            "run_fixed_batch assignments use roles absent from source adaptation"
        )


def _bind_managed_parent_route(
    binding_path: Path,
    *,
    request_session_id: str,
    attestation: object,
) -> SessionBinding:
    """Mint or verify the server-owned parent route under the binding lock."""
    expected_guards = tuple(sorted(MANAGED_CODEX_PARENT_GUARD_SET))
    config_digest = getattr(attestation, "hook_registry_digest", "")
    if not isinstance(config_digest, str) or not config_digest:
        raise SkillContractError("run_fixed_batch attestation lacks a managed config digest")
    with binding_lock(binding_path):
        current = read_binding(binding_path)
        if (
            current is None
            or current.session_id != request_session_id
            or not current.binding_valid
        ):
            raise SkillContractError(
                "run_fixed_batch request binding changed during authorization"
            )
        if current.managed_leaf_id:
            raise SkillContractError("run_fixed_batch is unavailable to managed leaf sessions")
        if current.managed_route == "":
            current = current._replace(
                managed_route="parent",
                managed_guard_set=expected_guards,
                managed_config_digest=config_digest,
            )
            write_binding(binding_path, current)
        if (
            current.managed_route != "parent"
            or current.managed_guard_set != expected_guards
            or current.managed_config_digest != config_digest
        ):
            raise SkillContractError(
                "run_fixed_batch binding does not match the managed parent route"
            )
        return current


def _request_facts(
    *,
    skill_name: str,
    request_context: Context,
    tool_ctx: Any,
) -> _ManagedRequestFacts:
    request_session_id = _request_session_identity(request_context)
    validate_session_id(request_session_id)
    normalized_skill_name = normalize_skill_name(skill_name)
    binding_path = resolve_binding_path(str(tool_ctx.project_dir), request_session_id)
    try:
        binding = read_binding(binding_path)
    except SessionBindingError as exc:
        raise SkillContractError("run_fixed_batch session binding is invalid") from exc
    if binding is None or binding.session_id != request_session_id or not binding.binding_valid:
        raise SkillContractError("run_fixed_batch requires a valid request session binding")
    if binding.managed_leaf_id:
        raise SkillContractError("run_fixed_batch is unavailable to managed leaf sessions")
    if not binding.managed_parent_id:
        raise SkillContractError("run_fixed_batch binding lacks a managed parent identity")
    selected_source = next(
        (
            entry
            for entry in reversed(binding.loaded_skills)
            if entry.skill_name == normalized_skill_name
        ),
        None,
    )
    if (
        selected_source is None
        or not selected_source.join_required
        or not selected_source.binding_valid
    ):
        raise SkillContractError("run_fixed_batch requires the exact loaded join-bearing skill")
    if not (
        selected_source.source_artifact_digest
        and selected_source.source_artifact_incarnation_id
        and selected_source.semantic_digest
        and selected_source.adaptation_digest
    ):
        raise SkillContractError(
            "run_fixed_batch source binding lacks immutable identity evidence"
        )
    backend = tool_ctx.backend
    authority = tool_ctx.managed_join_attestation_authority
    service = tool_ctx.managed_fixed_batch_service
    if backend is None or authority is None or service is None:
        raise SkillContractError("run_fixed_batch managed authority is unavailable")
    adaptation_context = authority.find_verified_context(
        backend=backend.name,
        parent_session_id=request_session_id,
    )
    if adaptation_context is None:
        raise SkillContractError("run_fixed_batch requires a current server-issued attestation")
    attestation = adaptation_context.managed_join_attestation
    if attestation is None or not service.recovery_ready:
        raise SkillContractError("run_fixed_batch is blocked by managed recovery")
    binding = _bind_managed_parent_route(
        binding_path,
        request_session_id=request_session_id,
        attestation=attestation,
    )
    return _ManagedRequestFacts(
        launch=ManagedLaunchBinding(
            request_session_id=request_session_id,
            managed_parent_id=binding.managed_parent_id,
            parent_session_id=attestation.parent_session_id,
            caller_key="pending",
            attestation_epoch=attestation.activation_epoch,
            recovery_ready=service.recovery_ready,
            selected_source=selected_source,
        ),
        binding=binding,
        selected_source=selected_source,
        channel_dir=binding_path.parent,
        adaptation_context=adaptation_context,
    )


@dataclass(slots=True)
class _ManagedLeafLaunchAdapter:
    tool_ctx: Any
    launch: ManagedLaunchBinding
    invocation: Any
    projection_context: Any
    source_name: str
    write_behavior: WriteBehaviorSpec
    read_only: bool
    adaptation: object

    def _write_leaf_binding(self, leaf_session_id: str, projection: object) -> None:
        attestation = getattr(
            getattr(self.projection_context, "adaptation_context", None),
            "managed_join_attestation",
            None,
        )
        config_digest = getattr(attestation, "hook_registry_digest", "")
        if not isinstance(config_digest, str) or not config_digest:
            raise SkillContractError("managed leaf launch lacks an attested config digest")
        assignment = getattr(getattr(projection, "binding", None), "assignment", None)
        assignment_id = getattr(assignment, "assignment_id", "")
        if not isinstance(assignment_id, str) or not assignment_id:
            raise SkillContractError("managed leaf launch lacks an assignment identity")
        selected = self.launch.selected_source
        if not isinstance(selected, LoadedSkillEntry):
            raise SkillContractError("managed leaf launch lacks a typed selected source entry")
        expected = SessionBinding(
            schema_version=SESSION_BINDING_SCHEMA_VERSION,
            session_id=leaf_session_id,
            join_required=True,
            binding_valid=True,
            artifact_digest=selected.source_artifact_digest,
            loaded_skills=(selected,),
            managed_parent_id=self.launch.managed_parent_id,
            managed_leaf_id=assignment_id,
            managed_route="leaf",
            managed_guard_set=tuple(sorted(MANAGED_CODEX_LEAF_GUARD_SET)),
            managed_config_digest=config_digest,
        )
        path = resolve_binding_path(str(self.tool_ctx.project_dir), leaf_session_id)
        with binding_lock(path):
            existing = read_binding(path)
            if existing is None:
                write_binding(path, expected)
            elif existing != expected:
                raise SkillContractError("managed leaf binding conflicts with an existing route")

    @asynccontextmanager
    async def __call__(
        self, projection, _permit
    ) -> AsyncIterator[ManagedLeafPreparedLaunch[ManagedLeafLaunchResult]]:
        manager = self.tool_ctx.session_skill_manager
        executor = self.tool_ctx.executor
        backend = self.tool_ctx.backend
        runner = self.tool_ctx.runner
        if manager is None or executor is None or backend is None:
            raise SkillContractError("managed leaf launch infrastructure is unavailable")
        adaptation = self.adaptation
        if not isinstance(adaptation, SkillSemanticAdaptationResult):
            raise SkillContractError("managed leaf launch has an invalid semantic adaptation")
        leaf_session_id = projection.binding.assignment.generated_home_id
        materialized = False
        source_cwd = Path(self.tool_ctx.project_dir)
        worktree = None
        if projection.binding.workspace.requires_isolated_worktree:
            if runner is None:
                raise SkillContractError("managed leaf worktree allocation requires a runner")
            revision = await runner(["git", "rev-parse", "HEAD"], cwd=source_cwd, timeout=10)
            if revision.returncode != 0 or not revision.stdout.strip():
                raise SkillContractError("managed leaf source revision could not be resolved")
            worktree_root = Path(self.tool_ctx.temp_dir) / "managed-leaf-worktrees"
            worktree = _ChildWorktreeRequest(
                project_root=source_cwd,
                worktree_root=worktree_root,
                worktree_path=worktree_root / leaf_session_id,
                revision=revision.stdout.strip(),
                runner=runner,
                create_worktree=_te_pkg.create_git_worktree,
                remove_worktree=_te_pkg.remove_git_worktree,
            )

        async def prepare(owned_cwd: Path):
            nonlocal materialized
            leaf_context = bind_projection_backend(
                build_fresh_projection_context(
                    str(owned_cwd),
                    self.invocation,
                    adaptation_context=self.projection_context.adaptation_context,
                ),
                backend,
                parent_sandbox_mode=self.projection_context.parent_sandbox_mode,
            )
            if backend.capabilities.managed_fixed_batch_route_capable:
                leaf_context = replace(leaf_context, managed_codex_route="leaf")
            source_document = project_agent_skill_document(
                self.invocation.root,
                leaf_context,
                semantic_adaptation=adaptation,
            )
            leaf_projection = project_managed_leaf(
                bind_managed_leaf(
                    assignment=projection.binding.assignment,
                    selected_source=self.launch.selected_source,
                    source_document=source_document,
                    adaptation=adaptation,
                    default_model=projection.binding.model,
                    write_behavior=self.write_behavior,
                    read_only=self.read_only,
                ),
                source_document,
            )
            add_dir = manager.materialize_invocation(
                leaf_session_id, self.invocation, leaf_context
            )
            materialized = True
            conventions = leaf_context.conventions
            if conventions is None:
                raise SkillContractError("managed leaf launch has no backend conventions")
            leaf_document = (
                Path(add_dir.path) / conventions.skills_subdir / self.source_name / "SKILL.md"
            )
            if not leaf_document.is_file():
                raise SkillContractError("managed leaf source document was not materialized")
            atomic_write(leaf_document, leaf_projection.prompt)
            if backend.capabilities.managed_fixed_batch_route_capable:
                self._write_leaf_binding(leaf_session_id, leaf_projection)
            return leaf_projection, add_dir, leaf_context

        request = _ChildResourceOwnerRequest(
            source_cwd=source_cwd,
            prepare=prepare,
            session_manager=manager,
            generated_home_id=leaf_session_id,
            generated_home_materialized=lambda: materialized,
            copied_snapshot_path=lambda: None,
            worktree=worktree,
        )
        async with _te_pkg.scoped_child_resource_owner(request) as child:
            leaf_projection, add_dir, leaf_context = child.value
            conventions = leaf_context.conventions
            assert conventions is not None
            leaf_document = (
                Path(add_dir.path) / conventions.skills_subdir / self.source_name / "SKILL.md"
            )
            if not leaf_document.is_file():
                raise SkillContractError("managed leaf source document was not materialized")

            async def execute() -> ManagedLeafLaunchResult:
                # The executor owns spawning: its subprocess runner delegates to
                # run_managed_async, which creates and settles the process group once.
                root = self.invocation.root
                source = getattr(root, "source_ref", None) or getattr(root, "source", None)
                if source is None:
                    raise SkillContractError("managed leaf invocation lacks source authority")
                result = await executor.run(
                    render_target_skill_command(f"/{self.source_name}", source, conventions),
                    str(child.owned_cwd),
                    model=leaf_projection.binding.model,
                    add_dirs=(add_dir,),
                    write_behavior=self.write_behavior,
                    readonly_skill=self.read_only,
                    backend_authority=BackendAuthority(
                        backend=backend.name,
                        kind=BackendAuthorityKind.GLOBAL,
                        tier=BackendAuthorityTier.GLOBAL,
                        key_path="managed_fixed_batch",
                    ),
                    caller_session_id=self.launch.parent_session_id,
                )
                return ManagedLeafLaunchResult(
                    outcome=OUTCOME_COMPLETED if result.success else OUTCOME_FAILED,
                    backend_session_id=result.session_id,
                    result_payload=result.to_json(),
                )

            # finalize is None: the leaf binding has no durable state to
            # release after execute(). scoped_child_resource_owner owns all
            # session/worktree cleanup, and the executor publishes the
            # result inside execute(). ManagedLeafPreparedLaunch.finalize
            # is Optional; the supervisor skips the call when None.

            yield ManagedLeafPreparedLaunch(
                ledger_attempt_evidence=leaf_projection.ledger_attempt_evidence,
                execute=execute,
            )


def _resolve_launch_binding(
    *,
    skill_name: str,
    assignments: tuple[ManagedLeafAssignmentInput, ...],
    idempotency_key: str,
    request_context: Context,
    tool_ctx: Any,
) -> ManagedFixedBatchLaunchBinding:
    facts = _request_facts(
        skill_name=skill_name,
        request_context=request_context,
        tool_ctx=tool_ctx,
    )
    caller_key = _text(
        idempotency_key,
        field="idempotency_key",
        maximum=_MAX_IDEMPOTENCY_KEY_CHARS,
    )
    if any(character not in _IDEMPOTENCY_KEY_CHARACTERS for character in caller_key):
        raise SkillContractError("run_fixed_batch idempotency_key has invalid characters")
    backend = tool_ctx.backend
    resolver = tool_ctx.skill_resolver
    if backend is None or resolver is None:
        raise SkillContractError("run_fixed_batch source resolver is unavailable")
    invocation = resolver.resolve_invocation(
        facts.selected_source.skill_name,
        tool_ctx.project_dir,
        SkillExecutionRole.SESSION,
        visibility=tool_ctx.config.skill_visibility_spec(),
        recipe_packs=tool_ctx.active_recipe_packs,
        recipe_features=tool_ctx.active_recipe_features,
    )
    projection_context = bind_projection_backend(
        build_fresh_projection_context(
            str(tool_ctx.project_dir),
            invocation,
            adaptation_context=facts.adaptation_context,
        ),
        backend,
        parent_sandbox_mode=(
            "read-only"
            if tool_ctx.read_only_resolver
            and tool_ctx.read_only_resolver(f"/{facts.selected_source.skill_name}")
            else "workspace-write"
        ),
    )
    if backend.capabilities.managed_fixed_batch_route_capable:
        projection_context = replace(projection_context, managed_codex_route="leaf")
    semantic_plan = invocation.root.semantic_plan
    if semantic_plan is None:
        raise SkillContractError("run_fixed_batch source lacks a semantic plan")
    adaptation = backend.adapt_skill_semantics(semantic_plan, facts.adaptation_context)
    if adaptation.validate_refusal_for(semantic_plan, backend=backend.name) is not None:
        raise SkillContractError(adaptation.diagnostic)
    adaptation.validate_for(semantic_plan, backend=backend.name)
    source_document = project_agent_skill_document(
        invocation.root,
        projection_context,
        semantic_adaptation=adaptation,
    )
    _validate_membership(assignments, facts.selected_source, adaptation)
    write_behavior = (
        tool_ctx.write_expected_resolver(f"/{facts.selected_source.skill_name}")
        if tool_ctx.write_expected_resolver
        else WriteBehaviorSpec()
    )
    read_only = bool(
        tool_ctx.read_only_resolver
        and tool_ctx.read_only_resolver(f"/{facts.selected_source.skill_name}")
    )
    launch = ManagedLaunchBinding(
        request_session_id=facts.launch.request_session_id,
        managed_parent_id=facts.launch.managed_parent_id,
        parent_session_id=facts.launch.parent_session_id,
        caller_key=caller_key,
        attestation_epoch=facts.launch.attestation_epoch,
        recovery_ready=facts.launch.recovery_ready,
        selected_source=facts.selected_source,
    )
    return ManagedFixedBatchLaunchBinding(
        launch=launch,
        flag_dir=facts.channel_dir,
        source_document=source_document,
        adaptation=adaptation,
        assignments=assignments,
        default_model=getattr(
            facts.adaptation_context.managed_join_attestation,
            "resolved_model",
            "",
        ),
        write_behavior=write_behavior,
        read_only=read_only,
        launch_leaf=_ManagedLeafLaunchAdapter(
            tool_ctx=tool_ctx,
            launch=launch,
            invocation=invocation,
            projection_context=projection_context,
            source_name=facts.selected_source.skill_name,
            write_behavior=write_behavior,
            read_only=read_only,
            adaptation=adaptation,
        ),
    )


async def _run_fixed_batch_handler(
    *,
    skill_name: str,
    assignments: object,
    idempotency_key: str,
    request_context: Context,
    tool_ctx: Any,
) -> dict[str, object]:
    try:
        normalized_assignments = _normalize_assignments(assignments)
        binding = _resolve_launch_binding(
            skill_name=skill_name,
            assignments=normalized_assignments,
            idempotency_key=idempotency_key,
            request_context=request_context,
            tool_ctx=tool_ctx,
        )
        service = tool_ctx.managed_fixed_batch_service
        if service is None:
            raise SkillContractError("run_fixed_batch supervisor is unavailable")
        result = await service.run(binding)
    except (OSError, ValueError, SkillContractError) as exc:
        return _deny(str(exc))
    return {
        "success": True,
        "batch_id": result.batch_id,
        "wave_outcome": result.wave_outcome,
        "replayed": result.replayed,
        "result_reference": result.result_reference,
        "result_digest": result.result_digest,
    }


def _page_payload(payload: object, *, offset: int, page_size: int) -> dict[str, object]:
    if type(offset) is not int or offset < 0:
        raise SkillContractError("read_fixed_batch_result offset must be a non-negative integer")
    if type(page_size) is not int or not 1 <= page_size <= _MAX_RESULT_PAGE_BYTES:
        raise SkillContractError(
            f"read_fixed_batch_result page_size must be between 1 and {_MAX_RESULT_PAGE_BYTES}"
        )
    rendered = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
    encoded = rendered.encode("utf-8")
    if offset > len(encoded):
        raise SkillContractError("read_fixed_batch_result offset exceeds the stored result")
    try:
        encoded[:offset].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillContractError("read_fixed_batch_result offset is not a UTF-8 boundary") from exc
    end = min(offset + page_size, len(encoded))
    while end > offset:
        try:
            content = encoded[offset:end].decode("utf-8")
            break
        except UnicodeDecodeError:
            end -= 1
    else:
        content = ""
    return {
        "content": content,
        "offset": offset,
        "next_offset": end if end < len(encoded) else None,
        "complete": end == len(encoded),
        "total_utf8_bytes": len(encoded),
    }


def _read_fixed_batch_result_handler(
    *,
    skill_name: str,
    batch_id: str,
    result_reference: str,
    assignment_id: str,
    offset: int,
    page_size: int,
    request_context: Context,
    tool_ctx: Any,
) -> dict[str, object]:
    try:
        facts = _request_facts(
            skill_name=skill_name,
            request_context=request_context,
            tool_ctx=tool_ctx,
        )
        service = tool_ctx.managed_fixed_batch_service
        if service is None:
            raise SkillContractError("read_fixed_batch_result supervisor is unavailable")
        payload = service.read_result(
            reference=_text(
                result_reference,
                field="result_reference",
                maximum=512,
            ),
            launch=facts.launch,
            batch_id=_text(batch_id, field="batch_id", maximum=512),
            assignment_id=_text(
                assignment_id,
                field="assignment_id",
                maximum=512,
                required=False,
            ),
        )
        return {"success": True, **_page_payload(payload, offset=offset, page_size=page_size)}
    except (OSError, ValueError, SkillContractError) as exc:
        return _deny(str(exc))


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core"},
    annotations={"readOnlyHint": False},
)
@_cancellation_shield()
@track_response_size("run_fixed_batch")
async def run_fixed_batch(
    skill_name: str,
    assignments: list[dict[str, str]],
    idempotency_key: str,
    ctx: Context = CurrentContext(),
) -> str:
    """Run one source-bound managed fixed batch and return an opaque aggregate reference.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        from autoskillit.server import _get_ctx  # circular-break: server context initializes tools

        return json.dumps(
            await _run_fixed_batch_handler(
                skill_name=skill_name,
                assignments=assignments,
                idempotency_key=idempotency_key,
                request_context=ctx,
                tool_ctx=_get_ctx(),
            ),
            sort_keys=True,
        )
    except Exception as exc:
        logger.error("run_fixed_batch_failed", exc_info=True)
        return json.dumps(_deny(f"{type(exc).__name__}: {exc}"), sort_keys=True)


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("read_fixed_batch_result")
async def read_fixed_batch_result(
    skill_name: str,
    batch_id: str,
    result_reference: str,
    assignment_id: str = "",
    offset: int = 0,
    page_size: int = _MAX_RESULT_PAGE_BYTES,
    ctx: Context = CurrentContext(),
) -> str:
    """Read an authorized bounded page from a managed fixed-batch result reference.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        from autoskillit.server import _get_ctx  # circular-break: server context initializes tools

        return json.dumps(
            _read_fixed_batch_result_handler(
                skill_name=skill_name,
                batch_id=batch_id,
                result_reference=result_reference,
                assignment_id=assignment_id,
                offset=offset,
                page_size=page_size,
                request_context=ctx,
                tool_ctx=_get_ctx(),
            ),
            sort_keys=True,
        )
    except Exception as exc:
        logger.error("read_fixed_batch_result_failed", exc_info=True)
        return json.dumps(_deny(f"{type(exc).__name__}: {exc}"), sort_keys=True)


__all__ = [
    "_read_fixed_batch_result_handler",
    "_run_fixed_batch_handler",
    "read_fixed_batch_result",
    "run_fixed_batch",
]
