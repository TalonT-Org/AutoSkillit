"""Fail-closed tool surface for behavioral evidence readers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

import anyio
import regex as re
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    DIRECT_PREFIX,
    EVIDENCE_READER_ENV_FORWARD_VARS,
    EVIDENCE_READER_TOOLS,
    HEADLESS_ENV_VAR,
    SessionType,
    SkillExecutionRole,
    agent_definition_digest,
    canonical_reader_tools_to_bare,
    get_logger,
    load_bundled_agent_definitions,
    session_type,
)
from autoskillit.execution import (
    CodexBackend,
    EvidenceReaderLaunchError,
    EvidenceReaderLaunchResult,
    EvidenceReaderResultStatus,
    evidence_reader_mcp_transport,
    evidence_reader_provider_environment,
    launch_evidence_reader,
)
from autoskillit.pipeline import ToolContext, create_background_task
from autoskillit.server import mcp
from autoskillit.server._explorer_projection import _explorer_launch_identity
from autoskillit.server._guards import _require_enabled
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._evidence_reader import (
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    EvidenceReaderError,
    EvidenceReaderInvocation,
    EvidenceReaderPage,
    StableArtifactCapture,
    capture_stable_artifact,
    create_evidence_reader_invocation,
    evidence_reader_scope_digest,
    load_evidence_reader_receipts,
    read_bound_evidence_reader_page,
    resolve_repository_identity,
    revoke_evidence_reader_invocation,
    stable_artifact_matches,
)

logger = get_logger(__name__)
_DEFAULT_PAGE_SIZE = 64_000
_MAX_PAGE_SIZE = 64_000
_BROKER_TIMEOUT_SECONDS = 5.0
_BROKER_UNAVAILABLE = "evidence_reader_broker_unavailable"
_DELEGATE_TIMEOUT_SECONDS = 300.0
_READER_POLICY = "read-only"
_PILOT_ROLE = "pr-source-reader"
_ROLE_DATA_KEYS = frozenset({"artifact_path", "requested_fields"})
_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")


class _DelegateError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _EvidenceReaderCancellationState:
    operation: str


_delegate_cancellation_state: ContextVar[_EvidenceReaderCancellationState] = ContextVar(
    "evidence_reader_delegate_cancellation_state"
)
_broker_cancellation_state: ContextVar[_EvidenceReaderCancellationState] = ContextVar(
    "evidence_reader_broker_cancellation_state"
)


def _delegate_cancelled(
    _state: _EvidenceReaderCancellationState,
    _exc: asyncio.CancelledError,
) -> str:
    return _delegate_outcome("cancelled", "reader_cancelled")


def _broker_cancelled(
    _state: _EvidenceReaderCancellationState,
    _exc: asyncio.CancelledError,
) -> str:
    return _delegate_outcome("error", "cancelled")


def _get_tool_context() -> ToolContext:
    from autoskillit.server import _get_ctx  # circular-break: server composition root

    return _get_ctx()


def _delegate_outcome(status: str, code: str) -> str:
    return json.dumps({"status": status, "code": code}, separators=(",", ":"))


# Explicit dispatch table for known non-rejected terminal outcomes.
# Substring matching would be forward-fragile: a future code like
# "cancellation_failed" would be misclassified as "cancelled". An
# explicit table forces every new code to be considered at the point
# of addition. Unmatched codes fall through to "rejected" (fail-closed
# default).
_DELEGATE_OUTCOMES: Final[Mapping[str, str]] = {
    "artifact_unsupported": "unsupported",
    "catalog_invalid": "unsupported",
    "catalog_probe_failed": "unsupported",
    "cli_probe_failed": "unsupported",
    "codex_unavailable": "unsupported",
    "output_schema_probe_failed": "unsupported",
    "platform_unsupported": "unsupported",
    "provider_auth_invalid": "unsupported",
    "deadline_exceeded": "timeout",
    "deadline_invalid": "timeout",
    "reader_deadline_exceeded": "timeout",
    "reader_cancelled": "cancelled",
    "cleanup_incomplete": "failed",
    "codex_execution_failed": "failed",
    "process_cleanup_incomplete": "failed",
    "reader_cleanup_failed": "failed",
}


def _delegate_error_outcome(code: str) -> str:
    status = _DELEGATE_OUTCOMES.get(code, "rejected")
    return _delegate_outcome(status, code)


def _page_payload(page: EvidenceReaderPage) -> str:
    return json.dumps(
        {
            "status": "ok",
            "content": page.content,
            "citation_id": page.citation_id,
            "byte_start": page.byte_start,
            "byte_end": page.byte_end,
            "line_start": page.line_start,
            "line_end": page.line_end,
            "snapshot_digest": page.snapshot_digest,
            "continuation": page.continuation,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _private_environment() -> dict[str, str]:
    return {
        name: os.environ[name] for name in EVIDENCE_READER_ENV_FORWARD_VARS if name in os.environ
    }


def _serve_page(
    *,
    bare_tool: str,
    page_size: int | None,
    continuation: str | None,
) -> str:
    deadline = time.monotonic() + _BROKER_TIMEOUT_SECONDS
    effective_page_size = _DEFAULT_PAGE_SIZE if page_size is None else page_size
    if (
        not isinstance(effective_page_size, int)
        or isinstance(effective_page_size, bool)
        or not 1 <= effective_page_size <= _MAX_PAGE_SIZE
    ):
        return _delegate_outcome("error", "page_size_invalid")
    from autoskillit.server import _get_ctx  # circular-break: server composition root

    try:
        page = read_bound_evidence_reader_page(
            _get_ctx(),
            _private_environment(),
            canonical_tool=f"{DIRECT_PREFIX}{bare_tool}",
            page_size=effective_page_size,
            continuation=continuation,
            deadline=deadline,
        )
    except EvidenceReaderError as exc:
        return _delegate_outcome("error", exc.code)
    return _page_payload(page)


def _role_request(role: str, role_data: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    if role != _PILOT_ROLE or not isinstance(role_data, dict):
        raise _DelegateError("reader_request_invalid")
    if set(role_data) != _ROLE_DATA_KEYS:
        raise _DelegateError("reader_request_invalid")
    artifact_path = role_data.get("artifact_path")
    raw_fields = role_data.get("requested_fields")
    if (
        not isinstance(artifact_path, str)
        or not artifact_path
        or len(artifact_path.encode("utf-8")) > 4_096
        or not isinstance(raw_fields, (list, tuple))
        or not 1 <= len(raw_fields) <= 32
    ):
        raise _DelegateError("reader_request_invalid")
    fields = tuple(raw_fields)
    if any(
        not isinstance(field, str)
        or not _FIELD_NAME.fullmatch(field)
        or len(field.encode("utf-8")) > 128
        for field in fields
    ) or len(fields) != len(set(fields)):
        raise _DelegateError("reader_request_invalid")
    return artifact_path, fields


def _reader_definition(role: str):
    definitions = tuple(
        definition for definition in load_bundled_agent_definitions() if definition.name == role
    )
    if len(definitions) != 1 or not definitions[0].reader_tools:
        raise _DelegateError("reader_role_unavailable")
    definition = definitions[0]
    try:
        bare_tools = canonical_reader_tools_to_bare(definition.reader_tools)
    except ValueError as exc:
        raise _DelegateError("reader_role_invalid") from exc
    definition_digest = agent_definition_digest(definition)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", definition_digest):
        raise _DelegateError("reader_role_invalid")
    return definition, bare_tools, definition_digest


def _trusted_repository(tool_ctx: ToolContext):
    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if not skill_name or tool_ctx.skill_resolver is None:
        raise _DelegateError("invocation_authority_unavailable")
    try:
        invocation = tool_ctx.skill_resolver.resolve_invocation(
            skill_name,
            tool_ctx.project_dir,
            SkillExecutionRole.SESSION,
            visibility=tool_ctx.config.skill_visibility_spec(),
            recipe_packs=tool_ctx.active_recipe_packs,
            recipe_features=tool_ctx.active_recipe_features,
        )
        launch_identity = _explorer_launch_identity(invocation)
    except Exception as exc:
        raise _DelegateError("invocation_authority_invalid") from exc
    if launch_identity is None:
        raise _DelegateError("invocation_authority_invalid")
    repository_root, _source_identity = launch_identity
    trusted_root = Path(tool_ctx.project_dir).resolve(strict=True)
    if repository_root != trusted_root:
        raise _DelegateError("invocation_authority_invalid")
    try:
        repository = resolve_repository_identity(trusted_root).repository_identity
        worktree_root = Path(repository.worktree_path).resolve(strict=True)
        common_git_dir = Path(repository.common_git_dir).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise _DelegateError("repository_identity_invalid") from exc
    if worktree_root != trusted_root or not common_git_dir.is_dir():
        raise _DelegateError("repository_identity_invalid")
    return trusted_root, worktree_root, common_git_dir


def _reader_transport(tool_ctx: ToolContext) -> dict[str, object]:
    backend = tool_ctx.backend
    if not isinstance(backend, CodexBackend) or backend.source_codex_home is None:
        raise _DelegateError("reader_backend_invalid")
    try:
        return evidence_reader_mcp_transport(backend.source_codex_home / "config.toml")
    except (OSError, ValueError, EvidenceReaderLaunchError) as exc:
        raise _DelegateError("reader_transport_invalid") from exc


def _delegate_caller_session(ctx: Context, tool_ctx: ToolContext) -> str:
    if (
        session_type() is not SessionType.SKILL
        or os.environ.get(HEADLESS_ENV_VAR) != "1"
        or not isinstance(tool_ctx.backend, CodexBackend)
    ):
        raise _DelegateError("reader_admission_denied")
    try:
        caller_session_id = str(ctx.session_id or "")
    except (AttributeError, RuntimeError) as exc:
        raise _DelegateError("caller_session_unavailable") from exc
    if not caller_session_id or caller_session_id.startswith("direct:"):
        raise _DelegateError("caller_session_unavailable")
    return caller_session_id


def _validate_child(
    result: EvidenceReaderLaunchResult,
    capture: StableArtifactCapture,
    invocation: EvidenceReaderInvocation,
    tool_ctx: ToolContext,
) -> dict[str, object]:
    try:
        payload = json.loads(result.payload_json)
    except json.JSONDecodeError as exc:
        raise _DelegateError("reader_result_invalid") from exc
    if not isinstance(payload, dict) or result.status not in {
        EvidenceReaderResultStatus.ANSWERED,
        EvidenceReaderResultStatus.PARTIAL,
        EvidenceReaderResultStatus.BLOCKED,
    }:
        raise _DelegateError("reader_result_incomplete")
    receipts = load_evidence_reader_receipts(tool_ctx, dict(invocation.environment))
    issued = {receipt.citation_id: receipt for receipt in receipts}
    for citation in result.citations:
        receipt = issued.get(citation.citation_id)
        if (
            receipt is None
            or citation.byte_start < receipt.byte_start
            or citation.byte_end > receipt.byte_end
            or not 0 <= citation.byte_start <= citation.byte_end <= len(capture.content)
        ):
            raise _DelegateError("citation_receipt_invalid")
        page = capture.content[citation.byte_start : citation.byte_end]
        line_start = capture.content[: citation.byte_start].count(b"\n") + 1
        line_end = line_start + page.count(b"\n") - int(page.endswith(b"\n"))
        if citation.line_start != line_start or citation.line_end != max(line_start, line_end):
            raise _DelegateError("citation_receipt_invalid")
    payload.pop("canary", None)
    return payload


def _delegate_sync(
    tool_ctx: ToolContext,
    *,
    caller_session_id: str,
    role: str,
    artifact_path: str,
    requested_fields: tuple[str, ...],
) -> str:
    deadline = time.monotonic() + _DELEGATE_TIMEOUT_SECONDS
    capture: StableArtifactCapture | None = None
    invocation: EvidenceReaderInvocation | None = None
    try:
        definition, bare_tools, definition_digest = _reader_definition(role)
        repository_root, worktree_root, common_git_dir = _trusted_repository(tool_ctx)
        capture = capture_stable_artifact(
            repository_root,
            artifact_path,
            deadline=deadline,
            max_attempts=3,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _DelegateError("reader_deadline_exceeded")
        invocation = create_evidence_reader_invocation(
            tool_ctx,
            capture,
            caller_session_id=caller_session_id,
            role=role,
            role_definition_digest=definition_digest,
            canonical_tools=definition.reader_tools,
            bare_tools=bare_tools,
            policy=_READER_POLICY,
            expires_at=time.time() + remaining,
        )
        environment = dict(invocation.environment)
        scope_digest = evidence_reader_scope_digest(tool_ctx, environment)
        provider_environment = evidence_reader_provider_environment()
        backend = getattr(tool_ctx, "backend", None)
        credential_file = (
            backend.source_codex_home / "auth.json"
            if isinstance(backend, CodexBackend) and backend.source_codex_home is not None
            else None
        )
        prompt = json.dumps(
            {
                "artifact_path": capture.artifact_path,
                "requested_fields": list(requested_fields),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        result = launch_evidence_reader(
            definition,
            invocation,
            prompt=prompt,
            mcp_transport=_reader_transport(tool_ctx),
            provider_env=provider_environment,
            credential_file=credential_file,
            repository_root=repository_root,
            worktree_root=worktree_root,
            common_git_dir=common_git_dir,
            expected_scope_digest=scope_digest,
            expected_snapshot_digest=capture.snapshot_digest,
            requested_fields=requested_fields,
            deadline=deadline,
        )
        payload = _validate_child(result, capture, invocation, tool_ctx)
        return json.dumps(
            {
                "status": result.status.value,
                "role": role,
                "artifact_path": capture.artifact_path,
                "snapshot_digest": capture.snapshot_digest,
                "result": payload,
                "conformance": (
                    asdict(result.conformance) if result.conformance is not None else None
                ),
            },
            separators=(",", ":"),
        )
    finally:
        original_exc = sys.exc_info()[1]
        terminal_error: _DelegateError | None = None
        if invocation is not None:
            try:
                revoke_evidence_reader_invocation(tool_ctx, dict(invocation.environment))
            except Exception as exc:
                logger.error("evidence reader authority cleanup failed", exc_info=True)
                terminal_error = _DelegateError("reader_cleanup_failed")
                terminal_error.__cause__ = exc
        if capture is not None:
            try:
                terminal = capture_stable_artifact(
                    capture.repository_root,
                    capture.artifact_path,
                    deadline=time.monotonic() + 15.0,
                    max_attempts=3,
                )
                if not stable_artifact_matches(capture, terminal):
                    terminal_error = terminal_error or _DelegateError("artifact_stale")
            except ArtifactCaptureError as exc:
                terminal_error = terminal_error or _DelegateError(
                    "artifact_stale"
                    if exc.status is ArtifactCaptureStatus.STALE
                    else "artifact_unsupported"
                )
            except Exception as exc:
                logger.error("evidence reader terminal recapture failed", exc_info=True)
                recapture_error = _DelegateError("artifact_unsupported")
                recapture_error.__cause__ = exc
                terminal_error = terminal_error or recapture_error
        if terminal_error is not None:
            if isinstance(original_exc, _DelegateError):
                raise terminal_error from original_exc
            raise terminal_error


async def _delegate_async(
    tool_ctx: ToolContext,
    *,
    caller_session_id: str,
    role: str,
    artifact_path: str,
    requested_fields: tuple[str, ...],
) -> str:
    """Await one exact owned delegation task through caller cancellation."""

    task = create_background_task(
        anyio.to_thread.run_sync(
            lambda: _delegate_sync(
                tool_ctx,
                caller_session_id=caller_session_id,
                role=role,
                artifact_path=artifact_path,
                requested_fields=requested_fields,
            ),
            abandon_on_cancel=False,
        ),
        label="evidence-reader-delegation",
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            try:
                await task
            except BaseException:
                logger.warning("cancelled evidence reader cleanup failed", exc_info=True)
        raise


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield(
    state_factory=lambda: _EvidenceReaderCancellationState("delegate"),
    state_context_var=_delegate_cancellation_state,
    response_factory=_delegate_cancelled,
)
async def delegate_evidence_reader(
    role: str,
    role_data: dict[str, object],
    ctx: Context = CurrentContext(),
) -> str:
    """Run one sterile reader against one stable repository artifact.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        tool_ctx = _get_tool_context()
        caller_session_id = _delegate_caller_session(ctx, tool_ctx)
        artifact_path, requested_fields = _role_request(role, role_data)
        return await _delegate_async(
            tool_ctx,
            caller_session_id=caller_session_id,
            role=role,
            artifact_path=artifact_path,
            requested_fields=requested_fields,
        )
    except _DelegateError as exc:
        return _delegate_error_outcome(exc.code)
    except ArtifactCaptureError as exc:
        code = (
            "artifact_stale"
            if exc.status is ArtifactCaptureStatus.STALE
            else "artifact_unsupported"
        )
        return _delegate_error_outcome(code)
    except (EvidenceReaderError, EvidenceReaderLaunchError) as exc:
        return _delegate_error_outcome(exc.code)
    except Exception:
        logger.warning("evidence reader delegation failed closed", exc_info=True)
        return _delegate_outcome("failed", "evidence_reader_delegation_failed")


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield(
    state_factory=lambda: _EvidenceReaderCancellationState("read"),
    state_context_var=_broker_cancellation_state,
    response_factory=_broker_cancelled,
)
async def read_authorized_artifact(page_size: int | None = None) -> str:
    """Read the initial immutable page for the launch-bound artifact.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        return _serve_page(
            bare_tool="read_authorized_artifact",
            page_size=page_size,
            continuation=None,
        )
    except Exception:
        logger.warning("authorized evidence artifact read failed closed", exc_info=True)
        return _delegate_outcome("error", _BROKER_UNAVAILABLE)


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield(
    state_factory=lambda: _EvidenceReaderCancellationState("page"),
    state_context_var=_broker_cancellation_state,
    response_factory=_broker_cancelled,
)
async def get_authorized_artifact_page(
    continuation: str,
    page_size: int | None = None,
) -> str:
    """Consume one opaque continuation for the launch-bound artifact.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        return _serve_page(
            bare_tool="get_authorized_artifact_page",
            page_size=page_size,
            continuation=continuation,
        )
    except Exception:
        logger.warning("authorized evidence artifact pagination failed closed", exc_info=True)
        return _delegate_outcome("error", _BROKER_UNAVAILABLE)
