"""Build callable tool catalogs through the server's real session boot paths."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import StrEnum
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.core import (
    ALL_VISIBILITY_TAGS,
    FLEET_DISPATCH_MODE,
    FLEET_DISPATCH_TOOLS,
    FLEET_MODE_ENV_VAR,
    FLEET_TOOLS,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    GATED_TOOLS,
    HEADLESS_AUTO_GATE_ENV_VAR,
    HEADLESS_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_FLEET,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SessionShape,
    ToolInitializationOperation,
    get_tool_def,
    session_shape,
    session_type,
)
from autoskillit.pipeline import ToolContext
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.lifecycle._session_scope import TOOL_SESSION_SCOPES


class CatalogContext(StrEnum):
    INTERACTIVE_SKILL = "interactive_skill"
    INTERACTIVE_ORCHESTRATOR = "interactive_orchestrator"
    FOOD_TRUCK = "food_truck"
    HEADLESS_SKILL = "headless_skill"
    HEADLESS_SKILL_AUTO_GATE = "headless_skill_auto_gate"
    FLEET_DISPATCH = "fleet_dispatch"


@dataclass(frozen=True)
class SessionCatalog:
    tools: frozenset[str]
    gate_open: bool
    shape: SessionShape

    def callable_tools(self) -> frozenset[str]:
        return frozenset(
            tool
            for tool in self.tools
            if (scope := TOOL_SESSION_SCOPES.get(tool)) is not None
            and scope.admits(self.shape)
            and (self.gate_open or tool not in GATED_TOOLS)
        )


def assert_no_fleet_mutation_leak(visible: Collection[str]) -> None:
    """Kitchen visibility may share fleet readers, never fleet mutations."""
    names = set(visible)
    assert names.isdisjoint(FLEET_TOOLS), f"Fleet-only tools visible: {names & FLEET_TOOLS}"
    mutations = {
        name
        for name in names & FLEET_DISPATCH_TOOLS
        if (definition := get_tool_def(name)) is None
        or definition.initialization_operation is not ToolInitializationOperation.INSPECTION
    }
    assert not mutations, f"Fleet dispatch mutations visible: {mutations}"


async def build_session_catalog(
    context: CatalogContext,
    *,
    monkeypatch: pytest.MonkeyPatch,
    build_ctx: Callable[..., ToolContext],
    packs: tuple[str, ...] | None = None,
) -> SessionCatalog:
    """Apply session tags and the matching lifespan boot before listing tools."""
    from autoskillit.server import _misc, mcp
    from autoskillit.server.lifecycle import _lifespan, _session_type
    from autoskillit.server.lifecycle._lifespan import _session_boots
    from autoskillit.server.tools import tools_kitchen

    if packs is not None and context is not CatalogContext.FOOD_TRUCK:
        raise ValueError("packs are valid only for FOOD_TRUCK")

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})

    session, headless_env, auto_gate, fleet_mode = {
        CatalogContext.INTERACTIVE_SKILL: (SESSION_TYPE_SKILL, None, None, None),
        CatalogContext.INTERACTIVE_ORCHESTRATOR: (SESSION_TYPE_ORCHESTRATOR, None, None, None),
        CatalogContext.FOOD_TRUCK: (SESSION_TYPE_ORCHESTRATOR, "1", None, None),
        CatalogContext.HEADLESS_SKILL: (SESSION_TYPE_SKILL, "1", None, None),
        CatalogContext.HEADLESS_SKILL_AUTO_GATE: (SESSION_TYPE_SKILL, "1", "1", None),
        CatalogContext.FLEET_DISPATCH: (SESSION_TYPE_FLEET, None, None, FLEET_DISPATCH_MODE),
    }[context]
    tool_tags = (
        ",".join(sorted(packs if packs is not None else ("kitchen-core",)))
        if context is CatalogContext.FOOD_TRUCK
        else None
    )

    for key, value in (
        (SESSION_TYPE_ENV_VAR, session),
        (HEADLESS_ENV_VAR, headless_env),
        (HEADLESS_AUTO_GATE_ENV_VAR, auto_gate),
        (FOOD_TRUCK_TOOL_TAGS_ENV_VAR, tool_tags),
        (FLEET_MODE_ENV_VAR, fleet_mode),
    ):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    assert _session_type._evidence_reader_binding_state() == "absent"
    assert not _session_type._has_explorer_binding_env()
    _session_type._apply_session_type_visibility()

    backend = MagicMock()
    backend.capabilities.supports_tool_list_changed = False
    ctx = build_ctx(backend=backend)
    ctx.gate = DefaultGateState(enabled=False)

    monkeypatch.setattr(tools_kitchen, "_write_hook_config", MagicMock())
    monkeypatch.setattr(_misc, "_prime_quota_cache", AsyncMock())
    monkeypatch.setattr(_session_boots, "_retain_kitchen_tracker_authority", MagicMock())
    monkeypatch.setattr(_session_boots, "register_active_kitchen", MagicMock(return_value=True))
    monkeypatch.setattr(_session_boots, "_activate_recipe_kitchen", MagicMock())
    monkeypatch.setattr(_lifespan, "create_background_task", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(_lifespan, "discover_campaign_state_files", MagicMock(return_value=[]))
    monkeypatch.setattr(_lifespan, "sweep_orphaned_tethers_async", AsyncMock())
    monkeypatch.setattr(_lifespan, "_reap_self_excluded_codex_and_daemon_orphans", MagicMock())

    boot = _lifespan._LIFESPAN_BOOT_REGISTRY[session_type()]
    assert boot is not None
    await boot(ctx)
    return SessionCatalog(
        tools=frozenset(tool.name for tool in await mcp.list_tools()),
        gate_open=ctx.gate.enabled,
        shape=session_shape(),
    )
