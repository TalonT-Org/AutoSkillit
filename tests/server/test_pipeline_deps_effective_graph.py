"""Regression coverage for dependency derivation from the effective recipe graph."""

from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.core import FinalizedRecipeProjection, RecipeFlowEdge
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


def test_deferred_guard_preserves_skip_only_route_in_finalized_projection(
    tool_ctx,
    tmp_path: Path,
) -> None:
    """A deferred guard retains the target reachable only through ``on_skip``."""
    fixture = Path(__file__).parents[1] / "recipe" / "fixtures" / "deferred_skip_only.yaml"
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    copyfile(fixture, recipes_dir / fixture.name)

    result = tool_ctx.recipes.load_and_validate(
        "deferred-skip-only",
        project_dir=tmp_path,
        temp_dir=tmp_path / "cache",
        defer_unresolved=True,
        include_finalized_projection=True,
    )

    assert result["valid"], result["errors"]
    projection = result["_finalized_projection"]
    assert isinstance(projection, FinalizedRecipeProjection)
    assert (
        RecipeFlowEdge(
            source="guarded",
            edge_type="skip",
            target="Y",
            condition="inputs.enabled",
            result_field=None,
        )
        in projection.ordered_flow_edges
    )
    assert "Y" in projection.ordered_step_names


def test_deferred_guard_reports_missing_skip_target_before_sweeping(
    tool_ctx,
    tmp_path: Path,
) -> None:
    """Deferred skip routes are validated before sweeping can hide an invalid target."""
    fixture = Path(__file__).parents[1] / "recipe" / "fixtures" / "deferred_skip_only.yaml"
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "deferred-skip-missing.yaml").write_text(
        fixture.read_text()
        .replace("name: deferred-skip-only", "name: deferred-skip-missing")
        .replace("on_skip: Y", "on_skip: missing_target")
    )

    result = tool_ctx.recipes.load_and_validate(
        "deferred-skip-missing",
        project_dir=tmp_path,
        temp_dir=tmp_path / "cache",
        defer_unresolved=True,
    )

    assert not result["valid"]
    assert any("missing_target" in error for error in result["errors"])


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
