"""Server-owned foundations for one fresh managed fixed-batch leaf.

This module deliberately contains no batch scheduling or MCP entry point.  It
provides the immutable facts a later supervisor needs and the cleanup scope
that both that supervisor and ``run_skill`` use for one child lifetime.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import time
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

import anyio

from autoskillit.core import (
    SkillContractError,
    SkillSemanticAdaptationResult,
    WriteBehaviorSpec,
    append_and_trim_jsonl,
    default_log_dir,
    destination_location,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import CleanupResult, SessionSkillManager, SubprocessRunner


logger = get_logger(__name__)

_MAX_CLEANUP_FAILURE_RECORDS = 5000
_MAX_CLEANUP_FAILURE_MESSAGE_CHARS = 1000
_PARENT_JOIN_INSTRUCTION = (
    "Use the server-owned managed fixed-batch route to declare, launch, and "
    "join the complete assignment set before parent synthesis."
)

_PreparedValue = TypeVar("_PreparedValue")


@dataclass(frozen=True, slots=True)
class _ChildWorktreeRequest:
    """The allocation facts for an isolated child worktree."""

    project_root: Path
    worktree_root: Path
    worktree_path: Path
    revision: str
    runner: SubprocessRunner
    create_worktree: Callable[[Path, Path, Path, str, SubprocessRunner], Awaitable[Path]]
    remove_worktree: Callable[[Path, Path, SubprocessRunner], Awaitable[CleanupResult]]


@dataclass(slots=True)
class _ChildResourceOwnerRequest(Generic[_PreparedValue]):
    """Inputs owned for the complete lifetime of one prepared child."""

    source_cwd: Path
    prepare: Callable[[Path], Awaitable[_PreparedValue]]
    session_manager: SessionSkillManager | None
    generated_home_id: str | None
    generated_home_materialized: Callable[[], bool]
    copied_snapshot_path: Callable[[], Path | None]
    worktree: _ChildWorktreeRequest | None = None
    cleanup_errors_are_terminal: bool = True


@dataclass(frozen=True, slots=True)
class _PreparedChildLaunch(Generic[_PreparedValue]):
    """A child whose cwd and preparation remain owned until scope exit."""

    owned_cwd: Path
    value: _PreparedValue


@dataclass(slots=True)
class ManagedLeafPreparedLaunch(Generic[_PreparedValue]):
    """A prepared fixed-batch leaf whose resource owner remains entered.

    ``execute`` deliberately delegates physical process ownership to the
    executor.  The fixed-batch supervisor owns only its ledger and permit.
    ``finalize`` is optional: adapters that have no post-execute cleanup
    (because ``scoped_child_resource_owner`` already owns session and
    worktree cleanup and the executor already publishes the result) can
    pass ``None``. The supervisor will skip the call when ``None``.
    """

    ledger_attempt_evidence: Mapping[str, str]
    execute: Callable[[], Awaitable[_PreparedValue]]
    finalize: Callable[[_PreparedValue], Awaitable[None]] | None = None


def _canonical(value: object) -> str:
    # ensure_ascii=True matches hooks/_join_ledger._canonical so digests
    # generated here (batch_id, assignment_id, first_run_id,
    # generated_home_id) compare equal across module boundaries.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _normalize(value: str, *, field: str, required: bool = True) -> str:
    if not isinstance(value, str):
        raise SkillContractError(f"managed leaf {field} must be a string")
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    if required and not normalized:
        raise SkillContractError(f"managed leaf {field} must be non-empty")
    return normalized


def _required_attribute(value: object, attribute: str, subject: str) -> str:
    result = getattr(value, attribute, None)
    if not isinstance(result, str) or not result:
        raise SkillContractError(f"managed leaf {subject} lacks {attribute}")
    return result


@dataclass(frozen=True, slots=True)
class ManagedLeafAssignmentInput:
    """The complete caller-independent input for one managed child."""

    role: str
    label: str
    task_prompt: str
    runtime_key: str = ""

    def __post_init__(self) -> None:
        _normalize(self.role, field="role")
        _normalize(self.label, field="label")
        _normalize(self.task_prompt, field="task_prompt")
        _normalize(self.runtime_key, field="runtime_key", required=False)

    @property
    def canonical_payload(self) -> Mapping[str, str]:
        return {
            "role": _normalize(self.role, field="role"),
            "label": _normalize(self.label, field="label"),
            "task_prompt": self.task_prompt,
            "runtime_key": _normalize(self.runtime_key, field="runtime_key", required=False),
        }


@dataclass(frozen=True, slots=True)
class ManagedLeafAssignmentIdentity:
    """Deterministic identity and prompt evidence for one declared child."""

    ordinal: int
    role: str
    label: str
    runtime_key: str
    task_prompt: str
    assignment_id: str
    first_run_id: str
    generated_home_id: str
    prompt_digest: str

    @property
    def ledger_membership(self) -> Mapping[str, object]:
        return {
            "ordinal": self.ordinal,
            "role": self.role,
            "label": self.label,
            "runtime_key": self.runtime_key,
            "prompt_digest": self.prompt_digest,
        }


@dataclass(frozen=True, slots=True)
class ManagedLeafIdentityPlan:
    """The side-effect-free full declaration plan for one managed batch."""

    batch_id: str
    membership_digest: str
    assignments: tuple[ManagedLeafAssignmentIdentity, ...]


def plan_managed_leaf_identities(
    request_key: str,
    assignments: Sequence[ManagedLeafAssignmentInput],
) -> ManagedLeafIdentityPlan:
    """Derive all pre-launch identities from server-owned bounded inputs."""
    normalized_request_key = _normalize(request_key, field="request_key")
    if not assignments:
        raise SkillContractError("managed leaf batch must declare at least one assignment")

    canonical_assignments = tuple(item.canonical_payload for item in assignments)
    labels = [str(item["label"]) for item in canonical_assignments]
    if len(labels) != len(set(labels)):
        raise SkillContractError(
            "managed leaf assignment labels must be unique after normalization"
        )

    batch_payload = {
        "request_key": normalized_request_key,
        "assignments": canonical_assignments,
    }
    batch_id = f"managed-batch-{_digest(batch_payload)[:24]}"
    planned: list[ManagedLeafAssignmentIdentity] = []
    for ordinal, assignment in enumerate(canonical_assignments):
        assignment_fact = {
            "batch_id": batch_id,
            "ordinal": ordinal,
            "role": assignment["role"],
            "label": assignment["label"],
            "runtime_key": assignment["runtime_key"],
        }
        assignment_id = f"{batch_id}:assignment-{_digest(assignment_fact)[:20]}"
        first_run_id = (
            f"managed-run-{_digest({'assignment_id': assignment_id, 'attempt': 0})[:24]}"
        )
        generated_home_payload = {
            "run_id": first_run_id,
            "assignment_id": assignment_id,
        }
        generated_home_id = f"managed-leaf-{_digest(generated_home_payload)[:24]}"
        prompt_digest = _digest({"task_prompt": assignment["task_prompt"]})
        planned.append(
            ManagedLeafAssignmentIdentity(
                ordinal=ordinal,
                role=str(assignment["role"]),
                label=str(assignment["label"]),
                runtime_key=str(assignment["runtime_key"]),
                task_prompt=str(assignment["task_prompt"]),
                assignment_id=assignment_id,
                first_run_id=first_run_id,
                generated_home_id=generated_home_id,
                prompt_digest=prompt_digest,
            )
        )
    return ManagedLeafIdentityPlan(
        batch_id=batch_id,
        membership_digest=_digest([item.ledger_membership for item in planned]),
        assignments=tuple(planned),
    )


@dataclass(frozen=True, slots=True)
class ManagedLeafWorkspacePlan:
    """Server-derived resource and retry classification for one leaf."""

    shared_workspace: bool
    requires_isolated_worktree: bool
    external_effect: str
    automatic_retry_allowed: bool


def classify_managed_leaf_workspace(
    *,
    read_only: bool,
    write_behavior: WriteBehaviorSpec,
) -> ManagedLeafWorkspacePlan:
    """Classify workspace and retry behavior without inventing a second policy."""
    if read_only:
        if write_behavior.external_effect != "none":
            raise SkillContractError("read-only managed leaf cannot declare an external effect")
        return ManagedLeafWorkspacePlan(True, False, "none", True)
    if write_behavior.mode is None:
        raise SkillContractError(
            "managed leaf with workspace writes requires a declared write_behavior adapter"
        )
    return ManagedLeafWorkspacePlan(
        shared_workspace=False,
        requires_isolated_worktree=True,
        external_effect=write_behavior.external_effect,
        automatic_retry_allowed=write_behavior.external_effect
        in {"none", "serialized-idempotent"},
    )


def may_retry_managed_leaf(
    workspace_plan: ManagedLeafWorkspacePlan,
    *,
    launched: bool,
    verified_non_execution: bool,
) -> bool:
    """Permit retries only before launch or after verified non-execution."""
    return workspace_plan.automatic_retry_allowed and (not launched or verified_non_execution)


@dataclass(frozen=True, slots=True)
class ManagedLeafBinding:
    """Server-owned source and semantic authority for one planned leaf."""

    assignment: ManagedLeafAssignmentIdentity
    source_artifact_digest: str
    source_artifact_incarnation_id: str
    source_projected_digest: str
    canonical_digest: str
    semantic_digest: str
    adaptation_digest: str
    model: str
    reasoning_effort: str | None
    workspace: ManagedLeafWorkspacePlan


@dataclass(frozen=True, slots=True)
class ManagedLeafProjection:
    """Leaf-only prompt and its independently bound projected artifact digest."""

    binding: ManagedLeafBinding
    prompt: str
    leaf_projection_artifact_digest: str
    resume_session_id: str = ""

    def __post_init__(self) -> None:
        if self.resume_session_id:
            raise SkillContractError("managed leaf projections must always launch without resume")

    @property
    def ledger_attempt_evidence(self) -> Mapping[str, str]:
        """The append-only evidence a ledger records after projection succeeds."""
        return {
            "generated_home_id": self.binding.assignment.generated_home_id,
            "leaf_projection_artifact_digest": self.leaf_projection_artifact_digest,
        }


def bind_managed_leaf(
    *,
    assignment: ManagedLeafAssignmentIdentity,
    selected_source: object,
    source_document: object,
    adaptation: SkillSemanticAdaptationResult,
    default_model: str,
    write_behavior: WriteBehaviorSpec,
    read_only: bool,
) -> ManagedLeafBinding:
    """Validate selected immutable authority and derive leaf-only launch policy."""
    if getattr(selected_source, "binding_valid", False) is not True:
        raise SkillContractError("managed leaf requires a valid selected source binding")
    source_identity = getattr(source_document, "source_identity", None)
    source_skill_name = _required_attribute(selected_source, "skill_name", "selected source")
    document_skill_name = _required_attribute(source_identity, "logical_name", "source document")
    if source_skill_name != document_skill_name:
        raise SkillContractError("managed leaf source binding does not match the source document")
    entry_projected = _required_attribute(selected_source, "projected_digest", "selected source")
    entry_canonical = _required_attribute(selected_source, "canonical_digest", "selected source")
    entry_semantic = _required_attribute(selected_source, "semantic_digest", "selected source")
    entry_adaptation = _required_attribute(selected_source, "adaptation_digest", "selected source")
    document_projected = _required_attribute(
        source_document, "projected_digest", "source document"
    )
    document_canonical = _required_attribute(
        source_document, "canonical_digest", "source document"
    )
    document_semantic = _required_attribute(source_document, "semantic_digest", "source document")
    document_adaptation = _required_attribute(
        source_document,
        "adaptation_digest",
        "source document",
    )
    for name, entry_value, document_value in (
        ("projected", entry_projected, document_projected),
        ("canonical", entry_canonical, document_canonical),
        ("semantic", entry_semantic, document_semantic),
        ("adaptation", entry_adaptation, document_adaptation),
    ):
        if not entry_value or entry_value != document_value:
            raise SkillContractError(
                f"managed leaf {name} identity does not match selected source"
            )
    if adaptation.digest != document_adaptation:
        raise SkillContractError("managed leaf adaptation is not the selected source adaptation")
    if adaptation.logical_role_mapping and assignment.role not in adaptation.logical_role_mapping:
        raise SkillContractError(
            f"managed leaf role {assignment.role!r} is not declared by source"
        )
    native_role = adaptation.logical_role_mapping.get(assignment.role, assignment.role)
    model, reasoning_effort = adaptation.model_effort_policy.get(
        native_role,
        (_normalize(default_model, field="default_model"), None),
    )
    return ManagedLeafBinding(
        assignment=assignment,
        source_artifact_digest=_required_attribute(
            selected_source, "source_artifact_digest", "selected source"
        ),
        source_artifact_incarnation_id=_required_attribute(
            selected_source, "source_artifact_incarnation_id", "selected source"
        ),
        source_projected_digest=document_projected,
        canonical_digest=document_canonical,
        semantic_digest=document_semantic,
        adaptation_digest=document_adaptation,
        model=model,
        reasoning_effort=reasoning_effort,
        workspace=classify_managed_leaf_workspace(
            read_only=read_only,
            write_behavior=write_behavior,
        ),
    )


def project_managed_leaf(
    binding: ManagedLeafBinding,
    source_document: object,
) -> ManagedLeafProjection:
    """Wrap a source-bound document without mutating it or importing parent context."""
    source_projected_digest = _required_attribute(
        source_document, "projected_digest", "source document"
    )
    if source_projected_digest != binding.source_projected_digest:
        raise SkillContractError("managed leaf projection source digest changed after binding")
    content = _required_attribute(source_document, "content", "source document")
    content = content.replace(f"- {_PARENT_JOIN_INSTRUCTION}", "")
    prompt = (
        content.rstrip()
        + "\n\n## Server-owned leaf assignment\n\n"
        + f"Role: {binding.assignment.role}\n"
        + f"Label: {binding.assignment.label}\n"
        + (
            f"Runtime key: {binding.assignment.runtime_key}\n"
            if binding.assignment.runtime_key
            else ""
        )
        + "\nTask:\n"
        + binding.assignment.task_prompt.rstrip()
        + "\n"
    )
    leaf_payload = {
        "assignment": {
            "assignment_id": binding.assignment.assignment_id,
            "first_run_id": binding.assignment.first_run_id,
            "generated_home_id": binding.assignment.generated_home_id,
            "prompt_digest": binding.assignment.prompt_digest,
        },
        "source": {
            "source_artifact_digest": binding.source_artifact_digest,
            "source_artifact_incarnation_id": binding.source_artifact_incarnation_id,
            "source_projected_digest": binding.source_projected_digest,
            "canonical_digest": binding.canonical_digest,
            "semantic_digest": binding.semantic_digest,
            "adaptation_digest": binding.adaptation_digest,
        },
        "launch": {
            "model": binding.model,
            "reasoning_effort": binding.reasoning_effort,
            "external_effect": binding.workspace.external_effect,
            "prompt": prompt,
        },
    }
    return ManagedLeafProjection(
        binding=binding,
        prompt=prompt,
        leaf_projection_artifact_digest=_digest(leaf_payload),
    )


def _record_cleanup_failure(session_id: str, path: str | None, exc: BaseException) -> None:
    log_path = default_log_dir() / "cleanup_failures.jsonl"
    record = {
        "ts": time.time(),
        "session_id": session_id,
        "path": path,
        "exception_type": type(exc).__name__,
        "message": str(exc)[:_MAX_CLEANUP_FAILURE_MESSAGE_CHARS],
    }
    try:
        append_and_trim_jsonl(log_path, _canonical(record), max_lines=_MAX_CLEANUP_FAILURE_RECORDS)
    except OSError:
        logger.warning("cleanup_failure_record_write_failed", session_id=session_id, exc_info=True)


@contextlib.asynccontextmanager
async def scoped_child_resource_owner(
    request: _ChildResourceOwnerRequest[_PreparedValue],
) -> AsyncIterator[_PreparedChildLaunch[_PreparedValue]]:
    """Own child cwd, preparation, and generated resources through finalization."""
    owned_worktree: Path | None = None
    body_error: BaseException | None = None
    try:
        if request.worktree is None:
            owned_cwd = request.source_cwd.resolve()
            if not owned_cwd.is_dir():
                raise SkillContractError(f"Child source cwd does not exist: {owned_cwd}")
        else:
            worktree = request.worktree
            owned_cwd = await worktree.create_worktree(
                worktree.project_root,
                worktree.worktree_root,
                worktree.worktree_path,
                worktree.revision,
                worktree.runner,
            )
            owned_worktree = owned_cwd
        prepared = await request.prepare(owned_cwd)
        yield _PreparedChildLaunch(owned_cwd=owned_cwd, value=prepared)
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        session_id = request.generated_home_id or "unknown-child-session"
        with anyio.CancelScope(shield=True):
            if request.generated_home_materialized():
                if request.session_manager is None or request.generated_home_id is None:
                    cleanup_errors.append(
                        SkillContractError(
                            "Generated home has no session-manager cleanup authority"
                        )
                    )
                else:
                    try:
                        request.session_manager.cleanup_session(request.generated_home_id)
                    except BaseException as exc:
                        logger.warning(
                            "session_skill_cleanup_failed",
                            session_id=request.generated_home_id,
                            exc_info=True,
                        )
                        _record_cleanup_failure(request.generated_home_id, None, exc)
                        cleanup_errors.append(exc)

            copied_snapshot = request.copied_snapshot_path()
            if copied_snapshot is not None and copied_snapshot.is_dir():
                try:
                    shutil.rmtree(copied_snapshot)
                except BaseException as exc:
                    logger.warning(
                        "snapshot_session_cleanup_failed",
                        path=str(copied_snapshot),
                        exc_info=True,
                    )
                    _record_cleanup_failure(session_id, str(copied_snapshot), exc)
                    cleanup_errors.append(exc)

            if owned_worktree is not None:
                cleanup_worktree = request.worktree
                assert cleanup_worktree is not None
                try:
                    destination = destination_location(owned_worktree)
                    trusted_root = destination_location(cleanup_worktree.worktree_root)
                    if not destination.is_relative_to(trusted_root):
                        raise SkillContractError(
                            f"Child worktree cleanup escapes trusted root: {destination}"
                        )
                    cleanup = await cleanup_worktree.remove_worktree(
                        destination,
                        cleanup_worktree.project_root,
                        cleanup_worktree.runner,
                    )
                except BaseException as exc:
                    logger.warning(
                        "child_worktree_cleanup_failed",
                        path=str(owned_worktree),
                        exc_info=True,
                    )
                    _record_cleanup_failure(session_id, str(owned_worktree), exc)
                    cleanup_errors.append(exc)
                else:
                    for path, detail in cleanup.failed:
                        failure = RuntimeError(
                            f"Child worktree cleanup failed for {path}: {detail}"
                        )
                        _record_cleanup_failure(session_id, path, failure)
                        cleanup_errors.append(failure)
        if body_error is None and cleanup_errors and request.cleanup_errors_are_terminal:
            raise BaseExceptionGroup("Child resource cleanup failed", cleanup_errors)


# Leading-underscore module: deliberate public surface for sibling modules
# under tools_execution and the server factory. Callers import names
# explicitly; public names are not re-exported via star imports.
