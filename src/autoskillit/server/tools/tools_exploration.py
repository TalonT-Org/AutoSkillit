"""Narrow, read-only broker tools for specialized repository explorers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit import consume_exploration_request_record
from autoskillit.core import (
    ContinuationCursor,
    EvidencePage,
    ExplorationContextStoreProtocol,
    ExplorationFailureCode,
    ExplorationQuerySpec,
    NodeKey,
    get_logger,
)
from autoskillit.core import (
    session_type as _resolve_session_type,
)
from autoskillit.pipeline import (
    EXPLORER_INELIGIBLE_SESSION_TYPES,
    CapabilityResolutionStatus,
    OwnerBoundExplorationContextStore,
    bind_session_scoped_durable,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

_MAX_QUERY_LENGTH = 4_096
_MAX_QUERY_RESULTS = 100
_MAX_RESPONSE_PAGE_SIZE = 100
_FAILURE_INVALID_REQUEST = ExplorationFailureCode.INVALID_REQUEST
_FAILURE_CONTEXT_UNAVAILABLE = ExplorationFailureCode.CONTEXT_UNAVAILABLE
_FAILURE_BROKER_UNAVAILABLE = ExplorationFailureCode.BROKER_UNAVAILABLE
_FAILURE_UNEXPECTED_INTERNAL_ERROR = ExplorationFailureCode.UNEXPECTED_INTERNAL_ERROR
logger = get_logger(__name__)


class BindSessionScopedFailed(Exception):
    """store.bind_session_scoped raised for a reason the store doesn't already name."""


class EnableComponentsFailed(Exception):
    """ctx.enable_components raised while granting exploration visibility."""


class StoreUnavailable(Exception):
    """No exploration context store is configured for this session."""


class _NodeKeyPayload(TypedDict):
    namespace: str
    value: str


class _NodePayload(TypedDict):
    key: _NodeKeyPayload
    label: str
    facts: list[str]
    inferences: list[str]
    unknowns: list[str]
    conflicts: list[str]
    evidence_ids: list[str]


class _EdgePayload(TypedDict):
    source: _NodeKeyPayload
    target: _NodeKeyPayload
    relationship: str
    facts: list[str]
    inferences: list[str]
    unknowns: list[str]
    conflicts: list[str]
    evidence_ids: list[str]


class _GraphPayload(TypedDict):
    nodes: list[_NodePayload]
    edges: list[_EdgePayload]
    conflicts: list[str]


def _failure(code: str) -> str:
    """Return a small, typed failure which discloses no repository state."""
    return json.dumps({"status": "error", "code": code}, separators=(",", ":"))


def _query(query: str, max_results: int) -> ExplorationQuerySpec | None:
    if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_LENGTH:
        return None
    if not isinstance(max_results, int) or isinstance(max_results, bool):
        return None
    try:
        return ExplorationQuerySpec(
            query=query,
            max_results=min(max_results, _MAX_QUERY_RESULTS),
        )
    except ValueError:
        return None


def _get_store() -> ExplorationContextStoreProtocol[object] | None:
    from autoskillit.server import _get_ctx  # circular-break: server composition root

    return _get_ctx().exploration_context_store


def _resolve_request_session(
    token: str,
    expected_tool_name: str,
    *,
    project_root: Path | None = None,
) -> str | None:
    from autoskillit.server import _get_ctx  # circular-break: server composition root

    root = _get_ctx().project_dir if project_root is None else project_root
    return consume_exploration_request_record(root, expected_tool_name, token)


def _try_session_scoped_submit(
    store: ExplorationContextStoreProtocol[object],
    request: ExplorationQuerySpec,
    session_id: str,
) -> EvidencePage | None:
    """Try session-scoped authority when launch-environment is unavailable."""
    if not isinstance(store, OwnerBoundExplorationContextStore):
        return None
    capability = store.session_scoped_capability(session_id)
    if capability is None:
        return None
    try:
        _replacement, page = store.submit_for_capability(
            capability=capability,
            query=request,
            page_size=min(request.max_results, _MAX_RESPONSE_PAGE_SIZE),
        )
        return page
    except (RuntimeError, ValueError):
        return None


def _try_session_scoped_page(
    store: ExplorationContextStoreProtocol[object],
    *,
    session_id: str,
    page_size: int,
    cursor: ContinuationCursor | None = None,
) -> EvidencePage | None:
    """Try session-scoped authority for page retrieval."""
    if not isinstance(store, OwnerBoundExplorationContextStore):
        return None
    capability = store.session_scoped_capability(session_id)
    if capability is None:
        return None
    try:
        status, page = store.get_page_for_capability(
            capability=capability,
            page_size=page_size,
            cursor=cursor,
        )
        return page if status is CapabilityResolutionStatus.OK else None
    except (RuntimeError, ValueError):
        return None


def _bounded_terms(values: tuple[str, ...]) -> list[str]:
    return [value[:512] for value in values[:16]]


def _node_key_payload(key: NodeKey) -> _NodeKeyPayload:
    return {"namespace": key.namespace[:512], "value": key.value[:512]}


def _graph_payload(page: EvidencePage) -> _GraphPayload:
    """Return the bounded, page-scoped canonical graph without dropping fanout."""

    return {
        "nodes": [
            {
                "key": _node_key_payload(node.key),
                "label": node.label[:512],
                "facts": _bounded_terms(node.facts),
                "inferences": _bounded_terms(node.inferences),
                "unknowns": _bounded_terms(node.unknowns),
                "conflicts": _bounded_terms(node.conflicts),
                "evidence_ids": [evidence_id[:512] for evidence_id in node.evidence_ids],
            }
            for node in page.graph_nodes
        ],
        "edges": [
            {
                "source": _node_key_payload(edge.source),
                "target": _node_key_payload(edge.target),
                "relationship": edge.relationship.value,
                "facts": _bounded_terms(edge.facts),
                "inferences": _bounded_terms(edge.inferences),
                "unknowns": _bounded_terms(edge.unknowns),
                "conflicts": _bounded_terms(edge.conflicts),
                "evidence_ids": [evidence_id[:512] for evidence_id in edge.evidence_ids],
            }
            for edge in page.graph_edges
        ],
        "conflicts": _bounded_terms(page.graph_conflicts),
    }


def _page_payload(page: EvidencePage, *, status: str) -> str:
    """Serialize a fixed-size typed evidence page without raw collector diagnostics."""
    payload = {
        "status": status,
        "result_digest": page.result_digest,
        "evidence": [
            {
                "id": record.evidence_id,
                "provenance": record.provenance.value,
                "snapshot_digest": record.snapshot_digest,
                "locator": record.locator,
                "method": record.method,
                "extractor_version": record.extractor_version,
                "searched_scope": _bounded_terms(record.searched_scope),
                "location": record.location,
                "query_uncertainty": _bounded_terms(record.query_uncertainty),
                "facts": _bounded_terms(record.facts),
                "inferences": _bounded_terms(record.inferences),
                "unknowns": _bounded_terms(record.unknowns),
                "conflicts": _bounded_terms(record.conflicts),
            }
            for record in page.evidence[:_MAX_RESPONSE_PAGE_SIZE]
        ],
        "complete": page.completeness.complete,
        "missing_collectors": list(page.completeness.missing_collectors[:16]),
        "failed_collectors": list(page.completeness.failed_collectors[:16]),
        "continuation": None if page.continuation is None else page.continuation.encode(),
        "graph": _graph_payload(page),
    }
    return json.dumps(payload, separators=(",", ":"))


def _fetch_page_from_launch_environment(
    *,
    page_size: int,
    cursor: ContinuationCursor | None = None,
    success_status: str,
) -> str:
    store = _get_store()
    if not 0 < page_size <= _MAX_RESPONSE_PAGE_SIZE:
        return _failure(_FAILURE_INVALID_REQUEST)
    if store is None:
        raise StoreUnavailable("exploration context store is unavailable")
    status, page = store.get_page_from_launch_environment(
        page_size=page_size,
        cursor=cursor,
    )
    if status is not CapabilityResolutionStatus.OK or page is None:
        return _failure(_FAILURE_CONTEXT_UNAVAILABLE)
    return _page_payload(page, status=success_status)


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def submit_exploration_query(
    query: str,
    max_results: int = _MAX_QUERY_RESULTS,
    _autoskillit_exploration_request_token: str = "",
) -> str:
    """Submit one bounded repository query to the server-owned exploration broker.

    Terminal authority is read from the server-owned launch environment. The
    optional internal parameter accepts only an opaque, server-issued, one-shot
    request token, never a raw session ID or capability.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        request = _query(query, max_results)
        store = _get_store()
        if request is None:
            return _failure(_FAILURE_INVALID_REQUEST)
        if store is None:
            raise StoreUnavailable("exploration context store is unavailable")
        status, page = store.submit_from_launch_environment(
            query=request,
            page_size=min(request.max_results, _MAX_RESPONSE_PAGE_SIZE),
        )
        if status is not CapabilityResolutionStatus.OK or page is None:
            session_id = (
                _resolve_request_session(
                    _autoskillit_exploration_request_token,
                    "submit_exploration_query",
                )
                if isinstance(store, OwnerBoundExplorationContextStore)
                else None
            )
            page = (
                None
                if session_id is None
                else _try_session_scoped_submit(store, request, session_id)
            )
            if page is None:
                return _failure(_FAILURE_CONTEXT_UNAVAILABLE)
        return _page_payload(page, status="accepted")
    except StoreUnavailable:
        return _failure(_FAILURE_BROKER_UNAVAILABLE)
    except Exception:  # truly unexpected — preserve the "Never raises" contract
        logger.warning("exploration query submission failed", exc_info=True)
        return _failure(_FAILURE_UNEXPECTED_INTERNAL_ERROR)


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def get_exploration_page(
    page_size: int = _MAX_RESPONSE_PAGE_SIZE,
    continuation: str | None = None,
    _autoskillit_exploration_request_token: str = "",
) -> str:
    """Retrieve bounded state for an active brokered exploration capability.

    The optional internal parameter accepts only an opaque, server-issued,
    one-shot request token, never a raw session ID or capability.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        cursor = None if continuation is None else ContinuationCursor.decode(continuation)
        result = _fetch_page_from_launch_environment(
            page_size=page_size,
            cursor=cursor,
            success_status="ready",
        )
        if result == _failure(_FAILURE_CONTEXT_UNAVAILABLE):
            store = _get_store()
            if isinstance(store, OwnerBoundExplorationContextStore):
                session_id = _resolve_request_session(
                    _autoskillit_exploration_request_token,
                    "get_exploration_page",
                )
                page = (
                    None
                    if session_id is None
                    else _try_session_scoped_page(
                        store,
                        session_id=session_id,
                        page_size=page_size,
                        cursor=cursor,
                    )
                )
                if page is not None:
                    return _page_payload(page, status="ready")
        return result
    except StoreUnavailable:
        return _failure(_FAILURE_BROKER_UNAVAILABLE)
    except Exception:  # truly unexpected — preserve the "Never raises" contract
        logger.warning("exploration page retrieval failed", exc_info=True)
        return _failure(_FAILURE_UNEXPECTED_INTERNAL_ERROR)


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def resume_exploration_context(
    page_size: int = _MAX_RESPONSE_PAGE_SIZE,
    _autoskillit_exploration_request_token: str = "",
) -> str:
    """Resume an active context through server-issued authority.

    The optional internal parameter accepts only an opaque, server-issued,
    one-shot request token, never a raw session ID or capability.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        result = _fetch_page_from_launch_environment(
            page_size=page_size,
            success_status="resumed",
        )
        if result == _failure(_FAILURE_CONTEXT_UNAVAILABLE):
            store = _get_store()
            if isinstance(store, OwnerBoundExplorationContextStore):
                session_id = _resolve_request_session(
                    _autoskillit_exploration_request_token,
                    "resume_exploration_context",
                )
                page = (
                    None
                    if session_id is None
                    else _try_session_scoped_page(
                        store,
                        session_id=session_id,
                        page_size=page_size,
                    )
                )
                if page is not None:
                    return _page_payload(page, status="resumed")
        return result
    except StoreUnavailable:
        return _failure(_FAILURE_BROKER_UNAVAILABLE)
    except Exception:  # truly unexpected — preserve the "Never raises" contract
        logger.warning("exploration context resumption failed", exc_info=True)
        return _failure(_FAILURE_UNEXPECTED_INTERNAL_ERROR)


@mcp.tool(
    tags={"autoskillit"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def enable_exploration(
    project_dir: str = "",
    _autoskillit_exploration_request_token: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Establish session-scoped exploration authority for Claude-native sessions.

    Call this before dispatching explorer subagents (Agent calls with
    subagent_type ``autoskillit:semantic-code-navigator`` or
    ``autoskillit:repository-impact-profiler``). The three broker tools
    become visible after this call succeeds.

    Not required for Codex sessions (per-child terminal binding) or for
    headless run_skill corridors (factory-based binding).

    The optional internal parameter accepts only an opaque, server-issued,
    one-shot request token, never a raw session ID or capability.

    Never raises.
    """
    try:
        session_type = _resolve_session_type()
        if session_type in EXPLORER_INELIGIBLE_SESSION_TYPES:
            return _failure(ExplorationFailureCode.SESSION_TYPE_INELIGIBLE)

        store = _get_store()
        if not isinstance(store, OwnerBoundExplorationContextStore):
            return _failure(ExplorationFailureCode.STORE_UNAVAILABLE)
        from autoskillit.server import _get_ctx  # circular-break: composition root

        cwd = Path(project_dir) if project_dir else _get_ctx().project_dir
        session_id = _resolve_request_session(
            _autoskillit_exploration_request_token,
            "enable_exploration",
            project_root=cwd,
        )
        if session_id is None:
            return _failure(ExplorationFailureCode.NO_SESSION_ID)
        repository_root = store.trusted_root
        # Durable, symmetric to bind_launch (which always writes a signed
        # authority file): bind_session_scoped alone is in-process-memory
        # only, lost on a server crash within the lease TTL. authority_home
        # is a per-session subdirectory under the project's temp dir — real,
        # writable, and unique per session_id so concurrent sessions never
        # collide on the fixed authority filename (#4684 Fix E).
        authority_home = _get_ctx().temp_dir / "exploration-session-authority" / session_id
        authority_home.mkdir(parents=True, exist_ok=True)
        try:
            bind_session_scoped_durable(
                store,
                authority_home=authority_home,
                owner_id=f"uid:{os.getuid()}",
                session_id=session_id,
                cwd=cwd,
                repository_root=repository_root,
                source_identity=f"interactive:{session_id}",
            )
        except (
            OwnerBoundExplorationContextStore.TrustedRootMismatch,
            OwnerBoundExplorationContextStore.InvalidSourceIdentity,
            OwnerBoundExplorationContextStore.ServiceNotConfigured,
            OwnerBoundExplorationContextStore.SnapshotStale,
            OwnerBoundExplorationContextStore.StoreClosed,
            OwnerBoundExplorationContextStore.CapacityExceeded,
        ):
            raise
        except Exception as exc:
            raise BindSessionScopedFailed(str(exc)) from exc
        exploration_enabled = False
        try:
            try:
                await ctx.enable_components(tags={"exploration"})
            except Exception as exc:
                raise EnableComponentsFailed(str(exc)) from exc
            exploration_enabled = True
        finally:
            # Symmetric grant/revoke: if the tag never became visible, undo
            # the lease too — a bound-but-invisible session is an orphan
            # authority; disable_components is the mirror of enable_components,
            # so a partial-success enable_components call never leaves the tag
            # visible without a live lease behind it (#4684 Fix E). Each
            # cleanup call is independently guarded so a failure in one
            # cannot mask the original in-flight exception or skip the other.
            if not exploration_enabled:
                try:
                    store.cleanup_session(session_id)
                except Exception:
                    logger.warning("enable_exploration_cleanup_session_failed", exc_info=True)
                try:
                    await ctx.disable_components(tags={"exploration"})
                except Exception:
                    logger.warning("enable_exploration_disable_components_failed", exc_info=True)
        return json.dumps(
            {"status": "ok", "exploration_enabled": True},
            separators=(",", ":"),
        )
    except OwnerBoundExplorationContextStore.TrustedRootMismatch:
        return _failure(ExplorationFailureCode.TRUSTED_ROOT_MISMATCH)
    except OwnerBoundExplorationContextStore.InvalidSourceIdentity:
        return _failure(ExplorationFailureCode.INVALID_SOURCE_IDENTITY)
    except OwnerBoundExplorationContextStore.ServiceNotConfigured:
        return _failure(ExplorationFailureCode.SERVICE_NOT_CONFIGURED)
    except OwnerBoundExplorationContextStore.SnapshotStale:
        return _failure(ExplorationFailureCode.SNAPSHOT_STALE)
    except OwnerBoundExplorationContextStore.StoreClosed:
        return _failure(ExplorationFailureCode.STORE_CLOSED)
    except OwnerBoundExplorationContextStore.CapacityExceeded:
        return _failure(ExplorationFailureCode.CAPACITY_EXCEEDED)
    except BindSessionScopedFailed:
        return _failure(ExplorationFailureCode.BIND_FAILED)
    except EnableComponentsFailed:
        return _failure(ExplorationFailureCode.ENABLE_COMPONENTS_FAILED)
    except Exception:  # truly unexpected — preserve the "Never raises" contract
        logger.warning("enable_exploration: unexpected", exc_info=True)
        return _failure(_FAILURE_UNEXPECTED_INTERNAL_ERROR)
