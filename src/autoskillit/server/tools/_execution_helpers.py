"""Subprocess coercion helpers for run_python."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import time
import types
import typing
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    RUN_PYTHON_SENTINEL_KEYS,
    SKILL_CAPABILITY_REGISTRY,
    WORKTREE_SKILLS,
    BackendCapabilities,
    CapturedStream,
    CodingAgentBackend,
    EffectiveSkillInvocationAuthority,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSessionContractStore,
    SkillSourceRef,
    SpillSpec,
    SubprocessResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
    extract_skill_name,
    get_logger,
    is_git_worktree,
    resolve_general_output_token_limit,
    resolve_temp_dir,
    spill_output,
)
from autoskillit.execution import CaptureReadError, SkillSessionContract, summarize_capture
from autoskillit.pipeline import canonical_step_name
from autoskillit.recipe import (
    OutcomeInvariantEntry,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    SuccessQualifierEntry,
)
from autoskillit.server._misc import SkillProjectionContext, _hook_config_overlay_path
from autoskillit.server._response_budget import shape_json_response
from autoskillit.server.tools._types import deny_envelope
from autoskillit.workspace import (
    EffectiveSkillDispatchContract,
    EffectiveSkillInvocation,
    SkillInfo,
    build_effective_skill_dispatch_contract,
    default_skill_resolver,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.core import SkillResult
    from autoskillit.pipeline import ToolContext

_PATH_LIKE_ARGS: frozenset[str] = frozenset({"output_dir", "workspace", "diagnostics_log_dir"})


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
) -> SkillProjectionContext:
    """Complete fresh projection authority after capability-driven backend selection."""
    return dataclasses.replace(
        context,
        backend=backend,
        conventions=backend.conventions if backend is not None else None,
    )


def build_validated_skill_dispatch_contract(
    resolved_command: str,
    projection_context: SkillProjectionContext,
    add_dirs: list[ValidatedAddDir],
    stored_contract: SkillSessionContract | None,
) -> EffectiveSkillDispatchContract:
    """Build immutable executor authority and verify resumed projected bytes."""
    contract = build_effective_skill_dispatch_contract(
        resolved_command,
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


def has_routing_capability(skill_caps: frozenset[str]) -> bool:
    """Return whether any declared capability is worker-routable."""
    return any(
        SKILL_CAPABILITY_REGISTRY.get(cap) is not None
        and SKILL_CAPABILITY_REGISTRY[cap].worker_routable
        for cap in skill_caps
    )


def get_routing_caps(skill_caps: frozenset[str]) -> list[str]:
    """Return the sorted worker-routable capabilities."""
    return sorted(
        cap
        for cap in skill_caps
        if SKILL_CAPABILITY_REGISTRY.get(cap) and SKILL_CAPABILITY_REGISTRY[cap].worker_routable
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
    completion_required: bool,
    skill_contract_json: str,
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
        expected_output_patterns=expected_output_patterns,
        write_behavior=write_behavior,
        read_only=read_only,
        completion_required=completion_required,
        skill_contract_json=skill_contract_json,
        projection_substitutions=tuple(sorted((projection_context.substitutions or {}).items())),
        projection_gating=projection_context.gating,
        projection_namespace=projection_context.namespace,
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
        read_only = data.get("read_only", False)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be a boolean")
        completion_required = data.get("completion_required", False)
        if not isinstance(completion_required, bool):
            raise ValueError("completion_required must be a boolean")
        return SkillContract(
            inputs=[SkillInput(**item) for item in data["inputs"]],
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
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SkillContractError("Persisted skill execution contract is invalid") from exc


def resolve_skill_dispatch_metadata(
    tool_ctx: ToolContext,
    skill_command: str,
    stored_contract: SkillSessionContract | None,
) -> tuple[list[str], WriteBehaviorSpec | None, SkillContract | None]:
    """Resolve fresh metadata or restore the exact persisted execution metadata."""
    if stored_contract is not None:
        return (
            list(stored_contract.expected_output_patterns),
            stored_contract.write_behavior,
            deserialize_skill_contract(stored_contract.skill_contract_json),
        )
    return (
        list(tool_ctx.output_pattern_resolver(skill_command))
        if tool_ctx.output_pattern_resolver
        else [],
        tool_ctx.write_expected_resolver(skill_command)
        if tool_ctx.write_expected_resolver
        else None,
        tool_ctx.skill_contract_resolver(skill_command)
        if tool_ctx.skill_contract_resolver
        else None,
    )


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
    contract.backend_requirements


def persist_run_skill_state(skill_result: SkillResult, project_dir: Path) -> None:
    from autoskillit.server._misc import persist_run_skill_state as persist  # circular-break

    persist(skill_result, project_dir)


def clear_run_skill_state(project_dir: Path) -> None:
    from autoskillit.server._misc import clear_run_skill_state as clear  # circular-break

    clear(project_dir)


def _spill_spec(tool_ctx: ToolContext) -> SpillSpec:
    budget = tool_ctx.config.output_budget
    return SpillSpec(
        inline_max_chars=budget.inline_max_chars,
        head_chars=budget.head_chars,
        tail_chars=budget.tail_chars,
    )


def run_cmd_artifact_root(tool_ctx: ToolContext, cwd: str) -> Path:
    if cwd and Path(cwd).is_absolute():
        return (
            resolve_temp_dir(Path(cwd).resolve(), tool_ctx.config.workspace.temp_dir) / "run_cmd"
        )
    return tool_ctx.temp_dir / "run_cmd"


def spill_run_cmd_result(
    tool_ctx: ToolContext,
    *,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    stdout_capture: CapturedStream | None = None,
    stderr_capture: CapturedStream | None = None,
    capture_error: str | None = None,
    execution_error: str | None = None,
) -> dict[str, object]:
    if capture_error is not None:
        result: dict[str, object] = {
            "success": False,
            "exit_code": returncode,
            "error": f"capture_failed: {capture_error}",
        }
        for stream_name, capture in [("stdout", stdout_capture), ("stderr", stderr_capture)]:
            if capture is not None:
                _process_capture_stream(result, stream_name, capture)
        return result

    if stdout_capture is not None or stderr_capture is not None:
        result = {
            "success": returncode == 0 and execution_error is None,
            "exit_code": returncode,
            "stdout": "",
            "stderr": "",
        }
        if execution_error:
            result["error"] = execution_error
        for stream_name, capture in [("stdout", stdout_capture), ("stderr", stderr_capture)]:
            if capture is not None:
                _process_capture_stream(result, stream_name, capture)
        return result

    artifact_root = run_cmd_artifact_root(tool_ctx, cwd)
    spec = _spill_spec(tool_ctx)
    shaped_stdout = spill_output(stdout, artifact_root, "stdout", spec)
    shaped_stderr = spill_output(stderr, artifact_root, "stderr", spec)
    result = {
        "success": returncode == 0,
        "exit_code": returncode,
        "stdout": shaped_stdout.text,
        "stderr": shaped_stderr.text,
    }
    if shaped_stdout.artifact_path is not None:
        result["stdout_artifact_path"] = shaped_stdout.artifact_path
    if shaped_stderr.artifact_path is not None:
        result["stderr_artifact_path"] = shaped_stderr.artifact_path
    return result


def _process_capture_stream(
    result: dict[str, object],
    stream_name: str,
    capture: CapturedStream,
) -> None:
    if capture.inline_text is not None:
        result[stream_name] = capture.inline_text
        try:
            capture.path.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        promoted_name = f"{stream_name}_{_uuid8()}.log"
        promoted = capture.path.parent / promoted_name
        try:
            os.replace(capture.path, promoted)
        except OSError as exc:
            result["success"] = False
            result["error"] = (
                f"capture_failed: promote {stream_name} artifact "
                f"{capture.path} -> {promoted}: {exc}"
            )
            return
        try:
            fd = os.open(str(promoted.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
        complete_str = "true" if capture.complete else "false"
        marker = (
            f"\n[spilled {capture.total_bytes} bytes -> {promoted}"
            f" sha256={capture.sha256} complete={complete_str}]\n"
        )
        result[stream_name] = capture.head + marker + capture.tail
        result[f"{stream_name}_artifact_path"] = str(promoted)
        result[f"{stream_name}_total_bytes"] = capture.total_bytes
        result[f"{stream_name}_sha256"] = capture.sha256


def _uuid8() -> str:
    return uuid.uuid4().hex[:8]


def _summarize_streams(
    sub_result: SubprocessResult,
    spec: SpillSpec,
    complete: bool,
) -> tuple[CapturedStream | None, CapturedStream | None, str | None]:
    stdout_capture = None
    stderr_capture = None
    capture_error: str | None = None
    for stream_name in ("stdout", "stderr"):
        stream_path = getattr(sub_result, f"{stream_name}_path")
        if stream_path is not None:
            try:
                cap = summarize_capture(stream_path, spec, complete=complete)
                if stream_name == "stdout":
                    stdout_capture = cap
                else:
                    stderr_capture = cap
            except CaptureReadError as exc:
                capture_error = f"{exc} [orphan={stream_path}]"
                try:
                    stream_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return stdout_capture, stderr_capture, capture_error


def shape_execution_response(
    tool_ctx: ToolContext,
    payload: dict[str, typing.Any],
    *,
    tool_name: str,
    work_dir: str,
) -> str:
    artifact_root = (
        resolve_temp_dir(Path(work_dir).resolve(), tool_ctx.config.workspace.temp_dir) / tool_name
        if work_dir and Path(work_dir).is_absolute()
        else tool_ctx.temp_dir / tool_name
    )
    selected_result_token_limit: int | None = None
    backend = getattr(tool_ctx, "backend", None)
    caps = getattr(backend, "capabilities", None) if backend is not None else None

    if isinstance(caps, BackendCapabilities):
        selected_result_token_limit = resolve_general_output_token_limit(caps)
    return shape_json_response(
        payload,
        tool_name=tool_name,
        artifact_dir=artifact_root,
        config=tool_ctx.config.output_budget,
        selected_result_token_limit=selected_result_token_limit,
    )


def validate_path_arg_anchoring(args: dict[str, object] | None, work_dir: str) -> str | None:
    """Return error message if args contain relative path-like values without work_dir."""
    if not args:
        return None
    for key in _PATH_LIKE_ARGS:
        val = args.get(key)
        if isinstance(val, str) and val and not Path(val).is_absolute() and not work_dir:
            if "work_dir" in args:
                return (
                    f"run_python: arg '{key}' is a relative path ({val!r}) "
                    f"and work_dir appears inside args instead of as a top-level "
                    f"parameter — move work_dir to the top-level run_python call"
                )
            return (
                f"run_python: arg '{key}' is a relative path ({val!r}) "
                f"but work_dir was not provided — pass work_dir to anchor it"
            )
    return None


def resolve_relative_path_args(args: dict[str, object], work_dir: str) -> dict[str, object]:
    """Anchor relative path arguments to work_dir."""
    resolved = dict(args)
    for key in _PATH_LIKE_ARGS:
        val = resolved.get(key)
        if isinstance(val, str) and val and not Path(val).is_absolute():
            resolved[key] = str(Path(work_dir) / val)
    return resolved


def maybe_promote_work_dir(args: dict[str, object] | None, work_dir: str) -> str:
    """Promote work_dir from args to tool level if misplaced by the LLM.

    Returns the (possibly updated) work_dir value. Does not modify args —
    the caller is responsible for removing the key from args after promotion.
    """
    if not args or work_dir or "work_dir" not in args:
        return work_dir
    candidate = args["work_dir"]
    if isinstance(candidate, str) and candidate:
        return candidate
    return work_dir


def _coerce_scalar(val: object, annotation: object) -> object:
    """Coerce val to match the annotated type.

    Handles str, int, float, and Optional[T] / T | None variants.
    Skips containers, unions, bool, and unconvertible values.
    """
    if isinstance(val, bool):
        return val

    actual = annotation

    # Unwrap X | None (types.UnionType — bare union syntax, Python 3.10+)
    if isinstance(annotation, types.UnionType):
        non_none = [a for a in annotation.__args__ if a is not type(None)]
        if len(non_none) == 1:
            actual = non_none[0]
    # Unwrap Optional[X] / Union[X, None] (typing.Union with __origin__)
    elif hasattr(annotation, "__origin__") and hasattr(annotation, "__args__"):
        ann: typing.Any = annotation
        origin = ann.__origin__
        args = ann.__args__
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                actual = non_none[0]

    # str ← int/float
    if actual is str and not isinstance(val, str):
        if isinstance(val, (int, float)):
            return str(val)
        return val
    # int ← str (try/except for unconvertible)
    if actual is int and not isinstance(val, int):
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                return val
        return val
    # float ← str/int (try/except for unconvertible)
    if actual is float and not isinstance(val, float):
        if isinstance(val, (str, int)):
            try:
                return float(val)
            except ValueError:
                return val
        return val
    return val


async def _import_and_call(
    dotted_path: str,
    args: dict[str, object] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Import a Python callable by dotted path and invoke it.

    Returns dict with 'success', 'result' (or 'error').
    Handles sync and async callables, with timeout protection.
    """
    import importlib
    import inspect

    if args is None:
        args = {}
    args = dict(args)

    if "." not in dotted_path:
        return {"success": False, "error": f"Invalid dotted path: {dotted_path!r}"}

    module_path, attr_name = dotted_path.rsplit(".", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return {"success": False, "error": f"Import failed for {module_path!r}: {exc}"}

    try:
        func = getattr(module, attr_name)
    except AttributeError:
        return {
            "success": False,
            "error": f"Module {module_path!r} has no attribute {attr_name!r}",
        }

    if not callable(func):
        return {"success": False, "error": f"{dotted_path!r} is not callable"}

    sig = inspect.signature(func)

    valid_keys = set(sig.parameters.keys())
    for key in list(args.keys()):
        if key in RUN_PYTHON_SENTINEL_KEYS and key not in valid_keys:
            logger.warning(
                "run_python stripped sentinel key from args",
                callable=dotted_path,
                arg_name=key,
            )
            del args[key]

    accepts_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if not accepts_var_keyword:
        for key in list(args.keys()):
            if key not in valid_keys:
                logger.warning(
                    "run_python dropped unrecognized arg",
                    callable=dotted_path,
                    arg_name=key,
                    extra_args=[key],
                )
                del args[key]

    try:
        type_hints = typing.get_type_hints(func)
    except (NameError, TypeError, AttributeError):
        logger.warning(
            "get_type_hints failed, skipping coercion", callable=dotted_path, exc_info=True
        )
        type_hints = {}
    coerced: dict[str, object] = {}
    for key, val in args.items():
        if val is None and key in sig.parameters:
            param = sig.parameters[key]
            if param.default is not inspect.Parameter.empty and param.default is not None:
                logger.warning(
                    "run_python null-arg coerced to default",
                    callable=dotted_path,
                    arg=key,
                    default=repr(param.default),
                )
                coerced[key] = param.default
                continue
        if val is not None and key in type_hints:
            annotation = type_hints[key]
            coerced_val = _coerce_scalar(val, annotation)
            if coerced_val is not val:
                logger.warning(
                    "run_python type coerced",
                    callable=dotted_path,
                    arg=key,
                    from_type=type(val).__name__,
                    to_type=type(coerced_val).__name__,
                )
                coerced[key] = coerced_val
                continue
        coerced[key] = val
    args = coerced

    try:
        if inspect.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(**args), timeout=timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(func, **args), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "run_python timed out; sync thread may continue running",
            dotted_path=dotted_path,
            timeout=timeout,
        )
        return {"success": False, "error": f"Timeout after {timeout}s calling {dotted_path}"}
    except Exception as exc:
        logger.warning(
            "run_python execution failed",
            dotted_path=dotted_path,
            error=type(exc).__name__,
            exc_info=True,
        )
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        json.dumps(result)
        return {"success": True, "result": result}
    except (TypeError, ValueError):
        return {"success": True, "result": str(result)}


def propagate_session_deadline(
    project_dir: Path, provider_extras: dict[str, str] | None
) -> dict[str, str] | None:
    """Propagate AUTOSKILLIT_SESSION_DEADLINE from the order overlay to L1 sessions.

    Fleet/food-truck sessions inherit the deadline via env_extras from fleet/_api.py;
    interactive "order" sessions must compute it here. The overlay is read directly
    (mirrors `_check_ingredient_locks`) — do NOT use `_build_config_snapshot`, which
    collapses explicit timeouts to the RunSkillConfig default of 7200.

    Mutates `provider_extras` in place (creating it if None) and returns it.
    Failures are swallowed silently (malformed overlay -> skip).
    """
    try:
        overlay_path = _hook_config_overlay_path(project_dir)
        if not overlay_path.exists():
            return provider_extras
        overlay = json.loads(overlay_path.read_text())
        order_section = overlay.get("order", {})
        if "timeout" not in order_section:
            return provider_extras
        existing_deadline = os.environ.get("AUTOSKILLIT_SESSION_DEADLINE")
        if existing_deadline:
            # Fleet session: preserve inherited deadline unchanged.
            deadline_str = existing_deadline
        else:
            # Order session: compute and cache deadline in process env.
            deadline = time.time() + int(order_section["timeout"])
            deadline_str = str(int(deadline))
            os.environ["AUTOSKILLIT_SESSION_DEADLINE"] = deadline_str
        if provider_extras is None:
            provider_extras = {}
        provider_extras["AUTOSKILLIT_SESSION_DEADLINE"] = deadline_str
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        pass  # malformed overlay — skip silently
    return provider_extras
