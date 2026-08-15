"""Fail-closed tool surface for behavioral evidence readers."""

from __future__ import annotations

from autoskillit.core import EVIDENCE_READER_ENV_FORWARD_VARS, get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

_AUTHORITY_UNAVAILABLE = (
    '{"status":"unsupported","code":"evidence_reader_authority_uninitialized",'
    '"message":"Evidence reader authority is not yet initialized."}'
)
logger = get_logger(__name__)


def _authority_contract_is_declared() -> bool:
    """Validate canonical authority input names without reading their values."""
    return bool(EVIDENCE_READER_ENV_FORWARD_VARS) and all(EVIDENCE_READER_ENV_FORWARD_VARS)


def _unsupported() -> str:
    if not _authority_contract_is_declared():
        logger.error("evidence reader authority input names are unavailable")
    return _AUTHORITY_UNAVAILABLE


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
    """Reject artifact reads until evidence-reader authority is initialized.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        return _unsupported()
    except Exception:
        logger.warning("authorized evidence artifact read failed closed", exc_info=True)
        return _AUTHORITY_UNAVAILABLE


@mcp.tool(
    tags={"autoskillit", "evidence-reader"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def get_authorized_artifact_page(
    continuation: str,
    page_size: int | None = None,
) -> str:
    """Reject artifact pagination until evidence-reader authority is initialized.

    Never raises.
    """
    try:
        if (gate := _require_enabled()) is not None:
            return gate
        return _unsupported()
    except Exception:
        logger.warning("authorized evidence artifact pagination failed closed", exc_info=True)
        return _AUTHORITY_UNAVAILABLE
