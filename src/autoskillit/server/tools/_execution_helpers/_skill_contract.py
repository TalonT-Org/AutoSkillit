"""Run skill session-contract lifecycle and serialization helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import typing
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    ExecutionIdentity,
    ExplorationVectorDef,
    LaunchSurface,
    ManagedHeadlessSessionLineageRef,
    ResolvedLaunchContract,
    SkillContractError,
    SkillExecutionRole,
    SkillSessionContractStore,
    SkillSourceRef,
    ValidatedAddDir,
    WriteBehaviorSpec,
    get_logger,
)
from autoskillit.execution import SkillSessionContract
from autoskillit.recipe import (
    AuditAuthorityPublicationSpec,
    AuditOutputMode,
    OutcomeInvariantEntry,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    SuccessQualifierEntry,
)
from autoskillit.server._misc import SkillProjectionContext
from autoskillit.workspace import (
    EffectiveSkillInvocation,
    SkillInfo,
    SkillResolver,
    default_skill_resolver,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, SkillResult


logger = get_logger(__name__)


@dataclasses.dataclass(slots=True)
class _RunSkillContractLifecycle:
    """Own provisional and finalized contract state across run_skill exits."""

    store: SkillSessionContractStore | None = None
    correlation_key: str | None = None
    bound_session_id: str | None = None
    retain_bound: bool = True
    execution_started: bool = False

    def observe_candidate(self, candidate_session_id: str) -> None:
        if self.store is not None and self.correlation_key is not None:
            self.store.observe_candidate(self.correlation_key, candidate_session_id)

    def bind_launch(self, launch_contract: ResolvedLaunchContract) -> None:
        if self.store is not None and self.correlation_key is not None:
            self.store.bind_launch(self.correlation_key, launch_contract)

    def finalize(self, session_id: str) -> None:
        if self.store is None or self.correlation_key is None:
            return
        if session_id:
            self.store.finalize(self.correlation_key, session_id)
            self.bound_session_id = session_id
        else:
            self.store.discard(self.correlation_key)
        self.correlation_key = None

    def apply_retention(self, needs_retry: bool) -> None:
        self.retain_bound = needs_retry
        if self.store is not None and self.bound_session_id is not None and not needs_retry:
            self.store.delete(self.bound_session_id)
            self.bound_session_id = None

    def rebind_final(
        self,
        final_session_id: str,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef,
    ) -> None:
        if (
            self.store is None
            or self.bound_session_id is None
            or self.bound_session_id == final_session_id
        ):
            return
        self.store.rebind_final_session(
            self.bound_session_id,
            final_session_id,
            managed_lineage_ref,
        )
        self.bound_session_id = final_session_id

    def cleanup(self) -> None:
        if self.store is not None and self.correlation_key is not None:
            try:
                self.store.discard(self.correlation_key)
            except Exception:
                logger.warning("skill_session_contract_discard_failed", exc_info=True)
        if (
            self.store is not None
            and self.bound_session_id is not None
            and self.execution_started
            and not self.retain_bound
        ):
            try:
                self.store.delete(self.bound_session_id)
            except Exception:
                logger.warning(
                    "skill_session_contract_delete_failed",
                    session_id=self.bound_session_id,
                    exc_info=True,
                )


def build_skill_session_contract(
    *,
    session_root: ValidatedAddDir,
    invocation: object,
    projection_context: SkillProjectionContext,
    resolved_command: str,
    expected_output_patterns: tuple[str, ...],
    write_behavior: WriteBehaviorSpec,
    read_only: bool,
    scope_discipline: bool,
    completion_required: bool,
    skill_contract_json: str,
    execution_identity: ExecutionIdentity,
) -> tuple[SkillSessionContract, dict[str, str]]:
    """Capture the exact projected invocation bytes before executor launch."""
    closure = tuple(getattr(invocation, "closure"))
    root = getattr(invocation, "root")
    backend = projection_context.backend
    conventions = projection_context.conventions
    if backend is None or conventions is None:
        raise SkillContractError("Projected invocation requires an effective backend")
    snapshot: dict[str, str] = {}
    projected_digests: dict[str, str] = {}
    canonical_digests: dict[str, str] = {}
    source_refs: dict[str, SkillSourceRef] = {}
    member_roles: dict[str, SkillExecutionRole] = {}
    member_capabilities: dict[str, frozenset[str]] = {}
    member_activate_deps: dict[str, tuple[str, ...]] = {}
    canonical_contents: dict[str, str] = {}
    exploration_vectors: dict[str, tuple[ExplorationVectorDef, ...]] = {}
    exploration_sidecar_digests: dict[str, str] = {}
    session_path = Path(session_root.path)
    for member in closure:
        relative_path = Path(conventions.skills_subdir) / member.name / "SKILL.md"
        content = (session_path / relative_path).read_text(encoding="utf-8")
        snapshot[relative_path.as_posix()] = content
        projected_digests[member.name] = hashlib.sha256(content.encode()).hexdigest()
        canonical_digests[member.name] = member.canonical_digest
        if member.source_ref is None or member.execution_role is None:
            raise SkillContractError(
                f"Effective invocation member {member.name!r} lacks typed identity"
            )
        source_refs[member.name] = member.source_ref
        member_roles[member.name] = member.execution_role
        member_capabilities[member.name] = member.uses_capabilities
        member_activate_deps[member.name] = member.activate_deps
        canonical_contents[member.name] = member.canonical_content
        exploration_vectors[member.name] = member.exploration_vectors
        exploration_sidecar_digests[member.name] = member.exploration_sidecar_digest
    project_root = getattr(invocation, "project_root")
    if project_root is None:
        raise SkillContractError("Effective invocation requires a project root")
    contract = SkillSessionContract(
        root_name=root.name,
        execution_role=getattr(invocation, "execution_role"),
        source_refs=source_refs,
        closure=tuple(member.name for member in closure),
        capability_union=getattr(invocation, "capability_union"),
        canonical_digests=canonical_digests,
        projected_digests=projected_digests,
        projection_version=projection_context.projection_version,
        project_root=str(project_root.resolve()),
        cwd=str(projection_context.cwd.resolve()),
        backend=backend.name,
        resolved_command=resolved_command,
        member_roles=member_roles,
        member_capabilities=member_capabilities,
        member_activate_deps=member_activate_deps,
        canonical_contents=canonical_contents,
        exploration_vectors=exploration_vectors,
        exploration_sidecar_digests=exploration_sidecar_digests,
        resolved_exploration_profile=projection_context.resolved_exploration_profile,
        active_exploration_applicabilities=(projection_context.active_exploration_applicabilities),
        expected_output_patterns=expected_output_patterns,
        write_behavior=write_behavior,
        read_only=read_only,
        scope_discipline=scope_discipline,
        parent_sandbox_mode=projection_context.parent_sandbox_mode,
        completion_required=completion_required,
        skill_contract_json=skill_contract_json,
        projection_substitutions=tuple(sorted((projection_context.substitutions or {}).items())),
        projection_gating=projection_context.gating,
        projection_namespace=projection_context.namespace,
        execution_identity=execution_identity,
    )
    return contract, snapshot


def serialize_skill_contract(skill_contract: object | None) -> str:
    """Serialize the resolved recipe contract into immutable resume state."""
    if skill_contract is None:
        return ""
    if isinstance(skill_contract, type) or not dataclasses.is_dataclass(skill_contract):
        raise SkillContractError("Resolved skill contract must be a dataclass")
    return json.JSONEncoder(sort_keys=True, separators=(",", ":")).encode(
        dataclasses.asdict(typing.cast(typing.Any, skill_contract))
    )


def deserialize_skill_contract(payload: str) -> SkillContract | None:
    """Reconstruct a recipe skill contract without consulting current metadata."""
    if not payload:
        return None
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("persisted skill execution contract must be an object")
        read_only = data.get("read_only", False)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        scope_discipline = data.get("scope_discipline", False)
        if not isinstance(scope_discipline, bool):
            raise ValueError("scope_discipline must be a boolean")
        completion_required = data.get("completion_required", False)
        if not isinstance(completion_required, bool):
            raise ValueError("completion_required must be a boolean")
        publication_data = data.get("audit_authority_publication")
        publication = None
        if isinstance(publication_data, dict):
            publication = AuditAuthorityPublicationSpec(**publication_data)
        raw_mode = data.get("audit_output_mode")
        return SkillContract(
            inputs=tuple(SkillInput(**item) for item in data["inputs"]),
            outputs=[SkillOutput(**item) for item in data["outputs"]],
            expected_output_patterns=list(data.get("expected_output_patterns", [])),
            pattern_examples=list(data.get("pattern_examples", [])),
            write_behavior=data.get("write_behavior"),
            write_expected_when=list(data.get("write_expected_when", [])),
            read_only=read_only,
            completion_required=completion_required,
            result_fields=[ResultFieldSpec(**item) for item in data.get("result_fields", [])],
            outcome_invariants=[
                OutcomeInvariantEntry(**item) for item in data.get("outcome_invariants", [])
            ],
            success_qualifiers=[
                SuccessQualifierEntry(**item) for item in data.get("success_qualifiers", [])
            ],
            input_preflight=data.get("input_preflight"),
            audit_authority_publication=publication,
            audit_output_mode=AuditOutputMode(raw_mode) if raw_mode is not None else None,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SkillContractError("Persisted skill execution contract is invalid") from exc


def rehydrate_skill_invocation(
    contract: SkillSessionContract,
    backend: CodingAgentBackend,
) -> tuple[EffectiveSkillInvocation, SkillProjectionContext]:
    """Rebuild the immutable effective graph exclusively from persisted state."""
    closure = tuple(
        SkillInfo(
            name=name,
            source=contract.source_refs[name].origin,
            path=contract.source_refs[name].skill_path,
            source_ref=contract.source_refs[name],
            uses_capabilities=contract.member_capabilities[name],
            execution_role=contract.member_roles[name],
            activate_deps=contract.member_activate_deps[name],
            exploration_vectors=contract.exploration_vectors[name],
            exploration_sidecar_digest=contract.exploration_sidecar_digests.get(name, ""),
            canonical_content=contract.canonical_contents[name],
            canonical_digest=contract.canonical_digests[name],
        )
        for name in contract.closure
    )
    by_name = {member.name: member for member in closure}
    invocation = EffectiveSkillInvocation(
        root=by_name[contract.root_name],
        closure=closure,
        capability_union=contract.capability_union,
        project_root=Path(contract.project_root),
        execution_role=contract.execution_role,
    )
    projection_context = SkillProjectionContext(
        cwd=Path(contract.cwd),
        invocation=invocation,
        backend=backend,
        conventions=backend.conventions,
        substitutions=dict(contract.projection_substitutions),
        gating=contract.projection_gating,
        namespace=contract.projection_namespace,
        exploration_launch_context_ref=(
            f"skill:{contract.root_name}"
            if any(member.exploration_vectors for member in closure)
            else None
        ),
        resolved_exploration_profile=contract.resolved_exploration_profile,
        active_exploration_applicabilities=contract.active_exploration_applicabilities,
        parent_sandbox_mode=contract.parent_sandbox_mode,
        projection_version=contract.projection_version,
    )
    return invocation, projection_context


def make_project_skill_resolver() -> SkillResolver:
    """Construct the standard project-aware resolver for a fresh dispatch."""
    return default_skill_resolver()


def validate_resumed_skill_contract(
    contract: SkillSessionContract,
    *,
    cwd: str,
    project_root: Path,
    backend: CodingAgentBackend | None,
) -> None:
    """Validate resume invariants that depend on the current dispatch context."""
    if contract.execution_role is not SkillExecutionRole.SESSION:
        raise SkillContractError(
            f"Resume contract role must be session, got {contract.execution_role.value!r}"
        )
    if contract.cwd != str(Path(cwd).resolve()) or contract.project_root != str(
        project_root.resolve()
    ):
        raise SkillContractError(
            "Resume contract cwd/project_root does not match the requested execution cwd"
        )
    if backend is None or contract.backend != backend.name:
        actual = backend.name if backend is not None else "unconfigured"
        raise SkillContractError(
            f"Resume contract backend {contract.backend!r} does not match {actual!r}"
        )
    launch_contract = contract.launch_contract
    if launch_contract is None:
        raise SkillContractError("Resume contract is missing physical launch authority")
    if contract.launch_contract_digest != launch_contract.digest:
        raise SkillContractError("Resume launch contract digest mismatch")
    if launch_contract.surface is not LaunchSurface.HEADLESS_SKILL:
        raise SkillContractError("Resume launch surface is not headless-skill")
    if launch_contract.effective_backend != contract.backend:
        raise SkillContractError("Resume launch backend does not match skill contract")
    if launch_contract.cwd != contract.cwd:
        raise SkillContractError("Resume launch cwd does not match skill contract")


def persist_run_skill_state(skill_result: SkillResult, project_dir: Path) -> None:
    from autoskillit.server._misc import persist_run_skill_state as persist  # circular-break

    persist(skill_result, project_dir)


def clear_run_skill_state(project_dir: Path) -> None:
    from autoskillit.server._misc import clear_run_skill_state as clear  # circular-break

    clear(project_dir)
