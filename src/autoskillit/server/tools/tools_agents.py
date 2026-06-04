from __future__ import annotations

import json

from fastmcp import Context
from fastmcp.dependencies import CurrentContext
from fastmcp.exceptions import ResourceError

from autoskillit.core import AGENT_PACK_REGISTRY, get_logger, pkg_root
from autoskillit.server import mcp
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)

AGENT_PACK_TAGS: dict[str, frozenset[str]] = {
    name: frozenset({name}) for name in AGENT_PACK_REGISTRY
}


@mcp.resource("agent://plan-review/{name}", tags={"plan-review"}, mime_type="text/markdown")
def get_plan_review_agent(name: str) -> str:
    """Return a bundled agent definition for plan review."""
    agent_path = pkg_root() / "agents" / f"{name}.md"
    if not agent_path.is_file():
        raise ResourceError(f"Unknown agent: {name}")
    content = agent_path.read_text()
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3 :].lstrip("\n")
    return content


@mcp.resource("agent://plan-review/_index", tags={"plan-review"}, mime_type="application/json")
def list_plan_review_agents() -> str:
    """List all available plan-review agents."""
    agents_dir = pkg_root() / "agents"
    agents = sorted(p.stem for p in agents_dir.glob("plan-*.md"))
    return json.dumps(agents)


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
async def unlock_agent_pack(pack_name: str, ctx: Context = CurrentContext()) -> str:
    """Unlock bundled agent definitions for this session.

    Makes agent definition resources readable via ReadMcpResourceTool.
    Scoped to this session only - does not affect other sessions.

    Never raises.
    """
    try:
        if pack_name not in AGENT_PACK_TAGS:
            return json.dumps({"success": False, "error": f"Unknown agent pack: {pack_name}"})
        tags = AGENT_PACK_TAGS[pack_name]
        await ctx.enable_components(tags=set(tags))
        return json.dumps(
            {"success": True, "pack": pack_name, "uri_prefix": f"agent://{pack_name}/"}
        )
    except Exception as exc:
        logger.warning("unlock_agent_pack failed", exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})
