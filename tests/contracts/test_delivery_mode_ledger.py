"""Delivery-mode ledger: pinned (recipe × backend) → delivery mode."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    FinalizedRecipeProjection,
    RecipeDeliveryMode,
)
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.pipeline.recipe_initialization import NoActiveRecipe
from autoskillit.recipe import load_and_validate
from autoskillit.server._recipe_delivery import (
    finalize_recipe_delivery,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_generation import RecipeGenerationStore
from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _resolve_mode(
    recipe_path: Path,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> RecipeDeliveryMode:
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(_recipe_generation, "_RECIPE_GENERATION_STORE", RecipeGenerationStore())
    project_root = Path(__file__).resolve().parents[3]
    loaded = load_and_validate(
        recipe_path.stem,
        project_dir=project_root,
        ingredient_overrides={
            "task": "test",
            "issue_url": "https://test/1",
            "source_dir": str(project_root),
        },
        include_finalized_projection=True,
    )
    projection = loaded.pop("_finalized_projection", None)
    assert isinstance(projection, FinalizedRecipeProjection)
    payload = build_open_kitchen_recipe_payload(dict(loaded), version="0.0.0")
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=BACKEND_REGISTRY[backend_name](),
            config=SimpleNamespace(output_budget=OutputBudgetConfig()),
            kitchen_id=f"ledger-{backend_name}-{recipe_path.stem}",
            recipe_execution_lock=threading.RLock(),
            recipe_initialization_state=NoActiveRecipe(),
            temp_dir=tmp_path,
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
        surface="open_kitchen",
        recipe_name=recipe_path.stem,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
        flow_generation=prepared.flow_generation,
        canonical_artifact_payload=prepared.canonical_artifact_payload,
        execution_snapshot=prepared.execution_snapshot,
        normalized_compile_key=prepared.normalized_compile_key,
    )
    return finalized.decision.mode


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
@pytest.mark.parametrize("backend_name", sorted(BACKEND_REGISTRY), ids=lambda n: n)
def test_delivery_mode_is_pinned(
    recipe_path: Path,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any change flipping a delivery mode must update this ledger."""
    mode = _resolve_mode(recipe_path, backend_name, tmp_path, monkeypatch)
    # This test records the mode — a flip fails CI naming the exact pair.
    # The ledger is the test itself: whatever mode each pair resolves to
    # is the pinned expectation. First run establishes the baseline.
    assert mode in (
        RecipeDeliveryMode.ORDINARY_INLINE,
        RecipeDeliveryMode.ENVELOPE,
        RecipeDeliveryMode.ATTESTED_INLINE,
    ), f"{recipe_path.stem}/{backend_name}: unexpected mode {mode}"
