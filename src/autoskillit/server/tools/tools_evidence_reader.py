"""Fail-closed tool surface for behavioral evidence readers."""

from __future__ import annotations

from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled

_AUTHORITY_UNAVAILABLE = (
    '{"status":"unsupported","code":"evidence_reader_authority_uninitialized",'
    '"message":"Evidence reader authority is not yet initialized."}'
)


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
async def delegate_evidence_reader(role: str, role_data: dict[str, object]) -> str:
    """Reject delegation until the server owns evidence-reader authority."""
    return _AUTHORITY_UNAVAILABLE


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
async def read_authorized_artifact(page_size: int | None = None) -> str:
    """Reject artifact reads until evidence-reader authority is initialized."""
    try:
        if (gate := _require_enabled()) is not None:
            return gate
    except Exception:
        return _AUTHORITY_UNAVAILABLE
    return _AUTHORITY_UNAVAILABLE


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
async def get_authorized_artifact_page(
    continuation: str,
    page_size: int | None = None,
) -> str:
    """Reject artifact pagination until evidence-reader authority is initialized."""
    try:
        if (gate := _require_enabled()) is not None:
            return gate
    except Exception:
        return _AUTHORITY_UNAVAILABLE
    return _AUTHORITY_UNAVAILABLE
