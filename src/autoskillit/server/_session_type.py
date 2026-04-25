"""Session-type tag visibility dispatcher for the FastMCP server.

Separated from server/__init__.py to satisfy the pure-facade constraint on
sub-package __init__ files.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from autoskillit.core import (
    CATEGORY_TAGS,
    FEATURE_REGISTRY,
    FEATURE_REVEAL_TAGS,
    FLEET_DISPATCH_MODE,
    FLEET_MODE_ENV_VAR,
    HEADLESS_ENV_VAR,
    SessionType,
    get_logger,
)
from autoskillit.core import session_type as _resolve_session_type

if TYPE_CHECKING:
    from fastmcp import FastMCP

_log = get_logger(__name__)

FeatureGate = Callable[["FastMCP", SessionType], None]


def _apply_session_type_visibility(
    *,
    feature_gates: list[FeatureGate] | None = None,
) -> None:
    """Apply FastMCP tag visibility based on session type + HEADLESS.

    Phase 1: session-type dispatch (existing logic, unchanged).
    Phase 2: feature gates — always run after phase 1, structurally
             enforcing feature gates as an override layer.
    """
    from autoskillit.server import mcp

    _session = _resolve_session_type()
    _headless = os.environ.get(HEADLESS_ENV_VAR) == "1"

    if _session is SessionType.FLEET:
        mcp.enable(tags={"fleet"})
        if os.environ.get(FLEET_MODE_ENV_VAR) == FLEET_DISPATCH_MODE:
            mcp.enable(tags={"fleet-dispatch"})
    elif _session is SessionType.ORCHESTRATOR and _headless:
        tool_tags = os.environ.get("AUTOSKILLIT_L2_TOOL_TAGS", "")
        if tool_tags:
            mcp.enable(tags={"kitchen-core"})
            for pack in tool_tags.split(","):
                pack = pack.strip()
                if not pack:
                    continue
                if pack not in CATEGORY_TAGS:
                    _log.warning(
                        "Unknown pack %r in AUTOSKILLIT_L2_TOOL_TAGS — skipping mcp.enable(); "
                        "valid packs: %s",
                        pack,
                        ", ".join(sorted(CATEGORY_TAGS)),
                    )
                    continue
                mcp.enable(tags={pack})
        else:
            mcp.enable(tags={"kitchen"})
    elif _session is SessionType.LEAF and _headless:
        mcp.enable(tags={"headless"})
    # ORCHESTRATOR+interactive and LEAF+interactive: no pre-reveal.
    # Cook unlocks via open_kitchen (orchestrator) or stays minimal (leaf).

    # Phase 2: feature gates — execute after phase 1 to enforce override semantics
    for gate in feature_gates or []:
        gate(mcp, _session)


def _fleet_gate(mcp: FastMCP, session: SessionType) -> None:  # noqa: ARG001
    """Disable fleet-tagged tools when the fleet feature is off.

    Intentionally reads only the AUTOSKILLIT_FEATURES__FLEET env var.
    This function runs at import time before config is loaded, so the
    config file is not available here. The deferred config-based check
    is performed by _fleet_auto_gate_boot() in _lifespan.py once the
    server context (ctx.config.features) is available.
    """
    fleet_val = os.environ.get("AUTOSKILLIT_FEATURES__FLEET", "").strip().lower()
    if fleet_val in ("false", "0", "no"):
        mcp.disable(tags=set(FEATURE_REVEAL_TAGS & FEATURE_REGISTRY["fleet"].tool_tags))
