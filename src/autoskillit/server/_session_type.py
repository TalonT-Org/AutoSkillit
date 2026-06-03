"""Session-type tag visibility dispatcher for the FastMCP server.

Separated from server/__init__.py to satisfy the pure-facade constraint on
sub-package __init__ files.
"""

from __future__ import annotations

import os
from typing import assert_never

from autoskillit.core import (
    CATEGORY_TAGS,
    FEATURE_REGISTRY,
    FLEET_DISPATCH_MODE,
    FLEET_MODE_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_AUTO_GATE_ENV_VAR,
    HEADLESS_ENV_VAR,
    SessionType,
    get_logger,
)
from autoskillit.core import session_type as _resolve_session_type

logger = get_logger(__name__)


def _collect_fleet_tool_tags() -> frozenset[str]:
    """Return the union of all tool_tags across FEATURE_REGISTRY entries."""
    return frozenset().union(*(fdef.tool_tags for fdef in FEATURE_REGISTRY.values()))


def _apply_session_type_visibility() -> None:
    """Apply FastMCP tag visibility based on session type + HEADLESS.

    Session-type dispatch only — feature gate suppression is handled at lifespan
    time by _fleet_auto_gate_boot (fleet sessions) and _redisable_subsets
    (open_kitchen sessions) where the full config pipeline is available.

    Non-headless interactive sessions rely on _pre_reveal_kitchen() at lifespan
    time for tag visibility when the backend does not support
    tools/list_changed notifications.
    """
    from autoskillit.server import mcp  # circular-break

    _session = _resolve_session_type()
    _headless = os.environ.get(HEADLESS_ENV_VAR) == "1"

    match _session:
        case SessionType.FLEET:
            fleet_tags = _collect_fleet_tool_tags()
            if fleet_tags:
                mcp.enable(tags=set(fleet_tags))
            if os.environ.get(FLEET_MODE_ENV_VAR) == FLEET_DISPATCH_MODE:
                mcp.enable(tags={"fleet-dispatch"})
        case SessionType.ORCHESTRATOR if _headless:
            tool_tags = os.environ.get(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "")
            if tool_tags:
                mcp.enable(tags={"kitchen-core"})
                for pack in tool_tags.split(","):
                    pack = pack.strip()
                    if not pack:
                        continue
                    if pack not in CATEGORY_TAGS:
                        logger.warning(
                            "Unknown pack %r in AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS"
                            " — skipping mcp.enable(); valid packs: %s",
                            pack,
                            ", ".join(sorted(CATEGORY_TAGS)),
                        )
                        continue
                    mcp.enable(tags={pack})
            else:
                mcp.enable(tags={"kitchen"})
        case SessionType.SKILL if _headless:
            mcp.enable(tags={"headless"})
            if os.environ.get(HEADLESS_AUTO_GATE_ENV_VAR) == "1":
                mcp.enable(tags={"kitchen-core"})
        case SessionType.ORCHESTRATOR | SessionType.SKILL:
            pass
        case _ as unreachable:
            assert_never(unreachable)
