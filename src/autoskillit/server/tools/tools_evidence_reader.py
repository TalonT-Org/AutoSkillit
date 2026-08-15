"""Fail-closed tool surface for behavioral evidence readers."""

from __future__ import annotations

import json
import os
import time

from autoskillit.core import DIRECT_PREFIX, EVIDENCE_READER_ENV_FORWARD_VARS, get_logger
from autoskillit.server import mcp
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._evidence_reader import (
    EvidenceReaderError,
    EvidenceReaderPage,
    read_bound_evidence_reader_page,
)

_AUTHORITY_UNAVAILABLE = (
    '{"status":"unsupported","code":"evidence_reader_authority_uninitialized",'
    '"message":"Evidence reader authority is not yet initialized."}'
)
logger = get_logger(__name__)
_DEFAULT_PAGE_SIZE = 64_000
_MAX_PAGE_SIZE = 64_000
_BROKER_TIMEOUT_SECONDS = 5.0
_BROKER_UNAVAILABLE = "evidence_reader_broker_unavailable"


def _authority_contract_is_declared() -> bool:
    """Validate canonical authority input names without reading their values."""
    return bool(EVIDENCE_READER_ENV_FORWARD_VARS) and all(EVIDENCE_READER_ENV_FORWARD_VARS)


def _unsupported() -> str:
    if not _authority_contract_is_declared():
        logger.error("evidence reader authority input names are unavailable")
    return _AUTHORITY_UNAVAILABLE


def _failure(code: str) -> str:
    return json.dumps({"status": "error", "code": code}, separators=(",", ":"))


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
        return _failure("page_size_invalid")
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
        return _failure(exc.code)
    return _page_payload(page)


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def delegate_evidence_reader(role: str, role_data: dict[str, object]) -> str:
    """Reject delegation until the server owns evidence-reader authority.

    Never raises.
    """
    try:
        return _unsupported()
    except Exception:
        logger.warning("evidence reader delegation failed closed", exc_info=True)
        return _AUTHORITY_UNAVAILABLE


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def read_authorized_artifact(page_size: int | None = None) -> str:
    """Read the initial immutable page for the launch-bound artifact.

    Never raises.
    """
    try:
        return _serve_page(
            bare_tool="read_authorized_artifact",
            page_size=page_size,
            continuation=None,
        )
    except Exception:
        logger.warning("authorized evidence artifact read failed closed", exc_info=True)
        return _failure(_BROKER_UNAVAILABLE)


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def get_authorized_artifact_page(
    continuation: str,
    page_size: int | None = None,
) -> str:
    """Consume one opaque continuation for the launch-bound artifact.

    Never raises.
    """
    try:
        return _serve_page(
            bare_tool="get_authorized_artifact_page",
            page_size=page_size,
            continuation=continuation,
        )
    except Exception:
        logger.warning("authorized evidence artifact pagination failed closed", exc_info=True)
        return _failure(_BROKER_UNAVAILABLE)
