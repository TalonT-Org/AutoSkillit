"""Leased hook roots for server-owned per-session backend homes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from autoskillit.core import (
    PluginLoadMode,
    SessionHookRoot,
    SkillContractError,
    plugin_launch_binding_scope,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend
    from autoskillit.pipeline import ToolContext


@contextmanager
def session_hook_root_scope(
    tool_ctx: ToolContext,
    backend: CodingAgentBackend | None,
) -> Iterator[SessionHookRoot | None]:
    """Hold a projection lease for the lifetime of one per-session backend home.

    Backends that bake no per-session config (``mcp_config_capable`` false)
    need no root. A generated-home backend never consumes the artifact through
    its load mode, so the projection is bound as ``PROJECTED_HOME``.
    """
    if backend is None or not backend.capabilities.mcp_config_capable:
        yield None
        return
    with plugin_launch_binding_scope(
        authority=tool_ctx.plugin_authority,
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    ) as binding:
        if binding is None:
            raise SkillContractError("projection binding unavailable for a per-session Codex home")
        yield SessionHookRoot.from_binding(binding)
