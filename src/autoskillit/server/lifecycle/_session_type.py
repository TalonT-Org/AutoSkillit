"""Session-type tag visibility dispatcher for the FastMCP server.

Separated from server/__init__.py to satisfy the pure-facade constraint on
sub-package __init__ files.
"""

from __future__ import annotations

import os
from typing import Literal, assert_never

from autoskillit.core import (
    CATEGORY_TAGS,
    EVIDENCE_READER_ENV_FORWARD_VARS,
    FEATURE_REGISTRY,
    FLEET_DISPATCH_MODE,
    FLEET_MODE_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_AUTO_GATE_ENV_VAR,
    SessionType,
    get_logger,
    session_shape,
)
from autoskillit.pipeline import (
    EXPLORATION_AUTHORITY_PATH_ENV,
    EXPLORATION_CAPABILITY_ENV,
    EXPLORATION_PRINCIPAL_ROLE,
    EXPLORATION_ROLE_ENV,
    EXPLORATION_SESSION_ENV,
    EXPLORER_ROLE_NAMES,
)

logger = get_logger(__name__)

_EXPLORER_BINDING_ENV_KEYS = frozenset(
    {
        EXPLORATION_CAPABILITY_ENV,
        EXPLORATION_ROLE_ENV,
        EXPLORATION_SESSION_ENV,
        EXPLORATION_AUTHORITY_PATH_ENV,
    }
)
_EvidenceReaderBindingState = Literal["absent", "candidate", "malformed"]


def _evidence_reader_binding_state() -> _EvidenceReaderBindingState:
    """Classify the reader identity environment without opening its authority."""
    present = {key for key in EVIDENCE_READER_ENV_FORWARD_VARS if key in os.environ}
    if not present:
        return "absent"
    if present != EVIDENCE_READER_ENV_FORWARD_VARS:
        return "malformed"
    if any(not os.environ[key] for key in EVIDENCE_READER_ENV_FORWARD_VARS):
        return "malformed"
    return "candidate"


def _has_explorer_binding_env() -> bool:
    """Return whether this MCP process is the bound endpoint of an explorer child."""
    values = {key: os.environ.get(key, "") for key in _EXPLORER_BINDING_ENV_KEYS}
    accepted_roles = EXPLORER_ROLE_NAMES | {EXPLORATION_PRINCIPAL_ROLE}
    return all(values.values()) and values[EXPLORATION_ROLE_ENV] in accepted_roles


def _collect_fleet_tool_tags() -> frozenset[str]:
    """Return only the feature tags authorized for fleet sessions."""
    return FEATURE_REGISTRY["fleet"].tool_tags


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

    _shape = session_shape()
    _session = _shape.tier
    _headless = _shape.headless

    if _evidence_reader_binding_state() != "absent" or _has_explorer_binding_env():
        # Restricted bindings gain visibility only after lifespan verifies their
        # authority. Environment variables alone never reveal tools or recipe resources
        # to these sessions.
        return

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
