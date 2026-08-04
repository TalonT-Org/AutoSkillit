"""Narrow, read-only broker tools for specialized repository explorers."""

from __future__ import annotations

import json

from autoskillit.core import ContinuationCursor, EvidencePage, ExplorationQuerySpec, get_logger
from autoskillit.pipeline import CapabilityResolutionStatus
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

_MAX_QUERY_LENGTH = 4_096
_MAX_PAGE_SIZE = 100
logger = get_logger(__name__)


def _failure(code: str) -> str:
    """Return a small, typed failure which discloses no repository state."""
    return json.dumps({"status": "error", "code": code}, separators=(",", ":"))


def _query(query: str, max_results: int) -> ExplorationQuerySpec | None:
    if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_LENGTH:
        return None
    if not isinstance(max_results, int) or isinstance(max_results, bool):
        return None
    try:
        return ExplorationQuerySpec(query=query, max_results=min(max_results, _MAX_PAGE_SIZE))
    except ValueError:
        return None


def _get_store():
    from autoskillit.server import _get_ctx  # circular-break: server composition root

    return _get_ctx().exploration_context_store


def _bounded_terms(values: tuple[str, ...]) -> list[str]:
    return [value[:512] for value in values[:16]]


def _node_key_payload(key) -> dict[str, str]:
    return {"namespace": key.namespace[:512], "value": key.value[:512]}


def _graph_payload(page: EvidencePage) -> dict[str, object]:
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
            for record in page.evidence[:_MAX_PAGE_SIZE]
        ],
        "complete": page.completeness.complete,
        "missing_collectors": list(page.completeness.missing_collectors[:16]),
        "failed_collectors": list(page.completeness.failed_collectors[:16]),
        "continuation": None if page.continuation is None else page.continuation.encode(),
        "graph": _graph_payload(page),
    }
    return json.dumps(payload, separators=(",", ":"))


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def submit_exploration_query(
    query: str,
    max_results: int = _MAX_PAGE_SIZE,
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
            return _failure("invalid_exploration_request")
        status, page = store.submit_from_launch_environment(
            query=request,
            page_size=request.max_results,
        )
        if status is not CapabilityResolutionStatus.OK or page is None:
            return _failure("exploration_context_unavailable")
        return _page_payload(page, status="accepted")
    except Exception:
        logger.warning("exploration query submission failed", exc_info=True)
        return _failure("exploration_broker_unavailable")


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def get_exploration_page(
    page_size: int = _MAX_PAGE_SIZE,
    continuation: str | None = None,
) -> str:
    """Retrieve bounded state for an active brokered exploration capability.

    The broker never accepts a caller-supplied context, role, or session.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        store = _get_store()
        if not 0 < page_size <= _MAX_PAGE_SIZE:
            return _failure("invalid_exploration_request")
        cursor = None if continuation is None else ContinuationCursor.decode(continuation)
        status, page = store.get_page_from_launch_environment(
            page_size=page_size,
            cursor=cursor,
        )
        if status is not CapabilityResolutionStatus.OK or page is None:
            return _failure("exploration_context_unavailable")
        return _page_payload(page, status="ready")
    except Exception:
        logger.warning("exploration page retrieval failed", exc_info=True)
        return _failure("exploration_broker_unavailable")


@mcp.tool(
    tags={"autoskillit", "kitchen", "exploration"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def resume_exploration_context(
    page_size: int = _MAX_PAGE_SIZE,
) -> str:
    """Resume an active context without accepting caller-supplied identity.

    Session resume mints a replacement launch capability in the server
    lifecycle; the tool receives no caller-supplied reopen authority.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        store = _get_store()
        if not 0 < page_size <= _MAX_PAGE_SIZE:
            return _failure("invalid_exploration_request")
        status, page = store.get_page_from_launch_environment(
            page_size=page_size,
        )
        if status is CapabilityResolutionStatus.OK and page is not None:
            return _page_payload(page, status="resumed")
        return _failure("exploration_context_unavailable")
    except Exception:
        logger.warning("exploration context resumption failed", exc_info=True)
        return _failure("exploration_broker_unavailable")
