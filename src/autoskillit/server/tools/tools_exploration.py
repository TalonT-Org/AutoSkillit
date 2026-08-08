"""Narrow, read-only broker tools for specialized repository explorers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from autoskillit.core import (
    ContinuationCursor,
    EvidencePage,
    ExplorationContextStoreProtocol,
    ExplorationQuerySpec,
    NodeKey,
    get_logger,
)
from autoskillit.pipeline import CapabilityResolutionStatus
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

_MAX_QUERY_LENGTH = 4_096
_MAX_QUERY_RESULTS = 100
_MAX_RESPONSE_PAGE_SIZE = 100
_FAILURE_INVALID_REQUEST = "invalid_exploration_request"
_FAILURE_CONTEXT_UNAVAILABLE = "exploration_context_unavailable"
_FAILURE_BROKER_UNAVAILABLE = "exploration_broker_unavailable"
logger = get_logger(__name__)


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


def _get_session_id() -> str | None:
    from autoskillit.server import _get_ctx  # circular-break: server composition root

    ctx = _get_ctx()
    return ctx.session_id if hasattr(ctx, "session_id") else None


def _try_session_scoped_submit(
    store: ExplorationContextStoreProtocol[object],
    request: ExplorationQuerySpec,
) -> EvidencePage | None:
    """Try session-scoped authority when launch-environment is unavailable."""
    from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

    if not isinstance(store, OwnerBoundExplorationContextStore):
        return None
    session_id = _get_session_id()
    if session_id is None:
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
    page_size: int,
    cursor: ContinuationCursor | None = None,
) -> EvidencePage | None:
    """Try session-scoped authority for page retrieval."""
    from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

    if not isinstance(store, OwnerBoundExplorationContextStore):
        return None
    session_id = _get_session_id()
    if session_id is None:
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
        raise RuntimeError("exploration context store is unavailable")
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
) -> str:
    """Submit one bounded repository query to the server-owned exploration broker.

    The capability, role, session, and reopen authority are read from the
    server-owned launch environment. The broker never accepts a repository
    path, command, or caller-supplied identity.

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
            raise RuntimeError("exploration context store is unavailable")
        status, page = store.submit_from_launch_environment(
            query=request,
            page_size=min(request.max_results, _MAX_RESPONSE_PAGE_SIZE),
        )
        if status is not CapabilityResolutionStatus.OK or page is None:
            page = _try_session_scoped_submit(store, request)
            if page is None:
                return _failure(_FAILURE_CONTEXT_UNAVAILABLE)
        return _page_payload(page, status="accepted")
    except Exception:
        logger.warning("exploration query submission failed", exc_info=True)
        return _failure(_FAILURE_BROKER_UNAVAILABLE)


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def get_exploration_page(
    page_size: int = _MAX_RESPONSE_PAGE_SIZE,
    continuation: str | None = None,
) -> str:
    """Retrieve bounded state for an active brokered exploration capability.

    The broker never accepts a caller-supplied context, role, or session.

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
            if store is not None:
                page = _try_session_scoped_page(store, page_size=page_size, cursor=cursor)
                if page is not None:
                    return _page_payload(page, status="ready")
        return result
    except Exception:
        logger.warning("exploration page retrieval failed", exc_info=True)
        return _failure(_FAILURE_BROKER_UNAVAILABLE)


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def resume_exploration_context(
    page_size: int = _MAX_RESPONSE_PAGE_SIZE,
) -> str:
    """Resume an active context without accepting caller-supplied identity.

    Session resume mints a replacement launch capability in the server
    lifecycle; the tool receives no caller-supplied reopen authority.

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
            if store is not None:
                page = _try_session_scoped_page(store, page_size=page_size)
                if page is not None:
                    return _page_payload(page, status="resumed")
        return result
    except Exception:
        logger.warning("exploration context resumption failed", exc_info=True)
        return _failure(_FAILURE_BROKER_UNAVAILABLE)


@mcp.tool(
    tags={"autoskillit"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def enable_exploration(
    project_dir: str = "",
) -> str:
    """Establish session-scoped exploration authority for Claude-native sessions.

    Call this before dispatching explorer subagents (Agent calls with
    subagent_type ``autoskillit:semantic-code-navigator`` or
    ``autoskillit:repository-impact-profiler``). The three broker tools
    become visible after this call succeeds.

    Not required for Codex sessions (per-child terminal binding) or for
    headless run_skill corridors (factory-based binding).

    Never raises.
    """
    try:
        from autoskillit.core import session_type as _resolve_session_type
        from autoskillit.pipeline.exploration_context import (
            EXPLORER_INELIGIBLE_SESSION_TYPES,
            OwnerBoundExplorationContextStore,
        )
        from autoskillit.server import _get_ctx

        session_type = _resolve_session_type()
        if session_type in EXPLORER_INELIGIBLE_SESSION_TYPES:
            return json.dumps(
                {"status": "error", "code": "session_type_ineligible"},
                separators=(",", ":"),
            )

        store = _get_ctx().exploration_context_store
        if not isinstance(store, OwnerBoundExplorationContextStore):
            return json.dumps(
                {"status": "error", "code": "exploration_store_unavailable"},
                separators=(",", ":"),
            )
        session_id = _get_session_id()
        if session_id is None:
            return json.dumps(
                {"status": "error", "code": "no_session_id"},
                separators=(",", ":"),
            )
        cwd = Path(project_dir) if project_dir else Path.cwd()
        repository_root = store.trusted_root
        store.bind_session_scoped(
            owner_id=f"uid:{os.getuid()}",
            session_id=session_id,
            cwd=cwd,
            repository_root=repository_root,
            source_identity=f"interactive:{session_id}",
        )
        mcp.enable(tags={"exploration"}, components={"tool"})
        return json.dumps(
            {"status": "ok", "exploration_enabled": True},
            separators=(",", ":"),
        )
    except Exception:
        logger.warning("exploration provisioning failed", exc_info=True)
        return json.dumps(
            {"status": "error", "code": "exploration_provisioning_failed"},
            separators=(",", ":"),
        )
