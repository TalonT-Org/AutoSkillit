"""Production-shaped recipe compilation helpers for delivery contracts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import RECIPE_DELIVERY_SURFACE_REGISTRY, FinalizedRecipeProjection
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.pipeline.recipe_initialization import NoActiveRecipe
from autoskillit.recipe import load_and_validate
from autoskillit.recipe.io import builtin_recipes_dir
from autoskillit.server._recipe_delivery import (
    finalize_recipe_delivery,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_generation import RecipeGenerationStore
from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload

BUNDLED_RECIPE_PATHS = tuple(sorted(builtin_recipes_dir().glob("*.yaml")))
CONTRACT_RECIPE_PATHS = tuple(sorted((builtin_recipes_dir() / "contracts").glob("*.yaml")))
ALL_DELIVERY_SURFACES = tuple(RECIPE_DELIVERY_SURFACE_REGISTRY)


def _payload_and_projection(
    recipe_path: Path,
) -> tuple[dict[str, object], FinalizedRecipeProjection]:
    project_root = Path(__file__).resolve().parents[3]
    loaded = load_and_validate(
        recipe_path.stem,
        project_dir=project_root,
        ingredient_overrides={
            "task": "test task",
            "issue_url": "https://github.com/test/test/issues/1",
            "source_dir": str(project_root),
        },
        include_finalized_projection=True,
    )
    projection = loaded.pop("_finalized_projection", None)
    assert isinstance(projection, FinalizedRecipeProjection)
    return build_open_kitchen_recipe_payload(dict(loaded), version="0.0.0"), projection


def compile_recipe(recipe_path: Path, surface: str, backend_name: str) -> str:
    """Render the ordinary production-shaped payload measured by fitness checks."""
    if surface not in RECIPE_DELIVERY_SURFACE_REGISTRY:
        raise KeyError(surface)
    if backend_name not in BACKEND_REGISTRY:
        raise KeyError(backend_name)
    payload, _ = _payload_and_projection(recipe_path)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def compile_bounded_page_plan(
    recipe_path: Path,
    surface: str,
    backend_name: str,
    *,
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_budget: OutputBudgetConfig | SimpleNamespace | None = None,
) -> dict[str, Any]:
    """Compile and return the real envelope manifest for one delivery pair.

    ``output_budget`` defaults to the tight stress-test shape (8_192-byte
    response ceiling) exercised by the existing fitness contracts. Pass an
    explicit ``OutputBudgetConfig()`` to compile under production defaults.
    """
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(_recipe_generation, "_RECIPE_GENERATION_STORE", RecipeGenerationStore())
    payload, projection = _payload_and_projection(recipe_path)
    resolved_output_budget = (
        output_budget
        if output_budget is not None
        else SimpleNamespace(
            response_max_bytes=8_192,
            page_max_bytes=OutputBudgetConfig().page_max_bytes,
        )
    )
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=BACKEND_REGISTRY[backend_name](),
            config=SimpleNamespace(output_budget=resolved_output_budget),
            kitchen_id=f"contract-{surface}-{backend_name}-{recipe_path.stem}",
            recipe_execution_lock=threading.RLock(),
            recipe_initialization_state=NoActiveRecipe(),
            temp_dir=temp_dir,
        ),
    )
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name=recipe_path.stem,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    finalized = finalize_recipe_delivery(
        payload,
        surface=surface,
        recipe_name=recipe_path.stem,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
        flow_generation=prepared.flow_generation,
        canonical_artifact_payload=prepared.canonical_artifact_payload,
        execution_snapshot=prepared.execution_snapshot,
        normalized_compile_key=prepared.normalized_compile_key,
    )
    result = json.loads(finalized.rendered)
    assert isinstance(result, dict)
    return result


def backend_forces_bounded(backend_name: str, surface: str) -> bool:
    """Return whether backend/surface policy requires envelope delivery."""
    capabilities = BACKEND_REGISTRY[backend_name]().capabilities
    definition = RECIPE_DELIVERY_SURFACE_REGISTRY[surface]
    return (
        definition.response_exemption_tool is None
        or capabilities.recipe_delivery_budget is not None
    )
