"""Session-type tag visibility dispatcher for the FastMCP server.

Separated from server/__init__.py to satisfy the pure-facade constraint on
sub-package __init__ files.
"""

from __future__ import annotations

import os

from autoskillit.core import HEADLESS_ENV_VAR, SessionType
from autoskillit.core import session_type as _resolve_session_type


def _apply_session_type_visibility() -> None:
    """Apply FastMCP tag visibility based on session type + HEADLESS."""
    from autoskillit.server import mcp

    _session = _resolve_session_type()
    _headless = os.environ.get(HEADLESS_ENV_VAR) == "1"

    if _session is SessionType.FRANCHISE:
        mcp.enable(tags={"franchise"})
    elif _session is SessionType.ORCHESTRATOR and _headless:
        mcp.enable(tags={"kitchen"})
    elif _session is SessionType.LEAF and _headless:
        mcp.enable(tags={"headless"})
    # ORCHESTRATOR+interactive and LEAF+interactive: no pre-reveal.
    # Cook unlocks via open_kitchen (orchestrator) or stays minimal (leaf).
