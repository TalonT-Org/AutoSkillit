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

    if _session is SessionType.FLEET or _session is SessionType.FRANCHISE:
        # T1 shim: FLEET maps to "franchise" tags until T2 renames the tag set.
        mcp.enable(tags={"franchise"})
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
    """Disable fleet-tagged tools when the fleet/franchise feature is off.

    Checks AUTOSKILLIT_FEATURES__FLEET first; falls back to
    AUTOSKILLIT_FEATURES__FRANCHISE for backward compatibility.
    FLEET value takes precedence when both are set.
    Safe to call at import time — no config or _ctx dependency.
    """
    fleet_val = os.environ.get("AUTOSKILLIT_FEATURES__FLEET", "").strip().lower()
    franchise_val = os.environ.get("AUTOSKILLIT_FEATURES__FRANCHISE", "").strip().lower()
    active_val = fleet_val if fleet_val else franchise_val
    if active_val in ("false", "0", "no"):
        mcp.disable(tags=set(FEATURE_REVEAL_TAGS & FEATURE_REGISTRY["fleet"].tool_tags))
