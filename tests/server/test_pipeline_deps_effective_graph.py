"""Regression coverage for dependency derivation from the effective recipe graph."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps
from autoskillit.server.tools.tools_kitchen import open_kitchen

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _request_context() -> MagicMock:
    """Build the FastMCP context surface used while opening a kitchen."""
    ctx = MagicMock()
    ctx.enable_components = AsyncMock()
    ctx.disable_components = AsyncMock()
    ctx.reset_visibility = AsyncMock()
    return ctx


async def _open_remediation(tool_ctx_kitchen_open, overrides: dict[str, str] | None) -> None:
    tool_ctx_kitchen_open.recipe_name = ""
    result = json.loads(
        await open_kitchen(
            name="remediation",
            overrides=overrides,
            ctx=_request_context(),
        )
    )
    assert result["success"] is True, result
    assert tool_ctx_kitchen_open.active_recipe_projection is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("overrides", "defer_from_defaults"),
    [
        ({"audit_impl": "false"}, False),
        ({"audit_impl": "false", "review_approach": "false"}, False),
        (None, True),
    ],
    ids=["audit-impl-skipped", "audit-and-review-skipped", "review-deferred"],
)
async def test_remediation_uses_only_effective_projection_predecessors(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str] | None,
    defer_from_defaults: bool,
) -> None:
    """Pruned and deferred remediation routes never derive dependencies from removed steps."""
    if defer_from_defaults:
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_kitchen.build_config_default_layer",
            lambda _defaults: {"audit_impl": "false"},
        )

    await _open_remediation(tool_ctx_kitchen_open, overrides)

    projection = tool_ctx_kitchen_open.active_recipe_projection
    assert projection is not None
    dependencies = _derive_phase_a_deps(projection)

    assert "make_plan" not in dependencies.get("dry_walkthrough", [])
    finalized_names = set(projection.ordered_step_names)
    assert all(
        predecessor in finalized_names
        for predecessors in dependencies.values()
        for predecessor in predecessors
    )


@pytest.mark.anyio
async def test_remediation_preserves_cycle_dependency_exemption(tool_ctx_kitchen_open) -> None:
    """The intentional cycle-member suppression policy remains unchanged."""
    await _open_remediation(tool_ctx_kitchen_open, {"audit_impl": "true"})

    projection = tool_ctx_kitchen_open.active_recipe_projection
    assert projection is not None
    assert _derive_phase_a_deps(projection) == {}
