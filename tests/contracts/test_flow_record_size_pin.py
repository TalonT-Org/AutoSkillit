"""Pin: implementation recipe flow_records page fits within 115,000 serialized chars.

Measures both the compiled byte count (from the envelope manifest) and the
delivered client-serialized character count of the flow_records content in its
flattened form (parsed JSON objects, not string-wrapped).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    FinalizedRecipeProjection,
    client_serialized_char_len,
    resolve_general_output_token_limit,
)
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.pipeline.recipe_initialization import NoActiveRecipe
from autoskillit.recipe import load_and_validate
from autoskillit.server._recipe_delivery import (
    persist_recipe_artifact,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_section_pagination import (
    build_recipe_section_page_plan,
    render_recipe_section_page,
    resolve_recipe_section_bound_bytes,
    select_recipe_section,
)
from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload
from tests.contracts.fixtures.recipes import (
    BUNDLED_RECIPE_PATHS,
    compile_bounded_page_plan,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_IMPL_PATH = next(p for p in BUNDLED_RECIPE_PATHS if p.stem == "implementation")

_MAX_FLOW_RECORD_SERIALIZED_CHARS = 115_000


def test_implementation_flow_records_within_115k_serialized_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = compile_bounded_page_plan(
        _IMPL_PATH,
        "open_kitchen",
        "codex",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
    )
    if envelope.get("delivery_bound_spill") is not True:
        pytest.skip("implementation resolves inline")
    flow_section = next(s for s in envelope["required_sections"] if s["section"] == "flow_records")
    compiled = flow_section["compiled_bytes"]
    assert compiled > 0
    assert compiled <= _MAX_FLOW_RECORD_SERIALIZED_CHARS, (
        f"implementation flow_records compiled_bytes={compiled} exceeds "
        f"{_MAX_FLOW_RECORD_SERIALIZED_CHARS} serialized-char pin"
    )


def test_implementation_flow_records_real_page_within_115k(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measure the actual implementation recipe's flow_records page.

    Builds a real page plan for the implementation recipe's flow_records
    section using the production pagination pipeline (not a synthetic envelope).
    Each rendered page's client-serialized char count must fit within 115K.
    """
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(
        _recipe_generation,
        "_RECIPE_GENERATION_STORE",
        _recipe_generation.RecipeGenerationStore(),
    )

    project_root = Path(__file__).resolve().parents[2]
    loaded = load_and_validate(
        "implementation",
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

    backend = BACKEND_REGISTRY["codex"]()
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=backend,
            config=SimpleNamespace(output_budget=OutputBudgetConfig()),
            kitchen_id="flow-pin-test",
            recipe_execution_lock=threading.RLock(),
            recipe_initialization_state=NoActiveRecipe(),
            temp_dir=tmp_path,
        ),
    )
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name="implementation",
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    flow_generation = prepared.flow_generation

    # Build the artifact so we have a RecipeArtifactGeneration
    generation = persist_recipe_artifact(
        tmp_path,
        kitchen_id="flow-pin-test",
        producer_tool="open_kitchen",
        recipe_name="implementation",
        payload=prepared.canonical_artifact_payload,
        flow_generation=flow_generation,
    )

    # Select the flow_records section from the persisted artifact's payload
    artifact_payload = json.loads(
        (tmp_path / "recipe-delivery").rglob("payload.json").__next__().read_text("utf-8")
    )
    selected = select_recipe_section(artifact_payload, "flow_records")
    assert selected.present, "flow_records section absent from implementation recipe"

    # Use production-default bounds
    conservative_limit = resolve_general_output_token_limit(backend.capabilities)
    bound_bytes = resolve_recipe_section_bound_bytes(
        OutputBudgetConfig().response_max_bytes,
        conservative_limit,
        OutputBudgetConfig().page_max_bytes,
    )

    page_plan = build_recipe_section_page_plan(
        kitchen_id="flow-pin-test",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=bound_bytes,
    )

    # Measure each real rendered page
    assert page_plan.total_parts > 0, "page plan has no pages"
    for part in range(page_plan.total_parts):
        rendered = render_recipe_section_page(page_plan, part)
        page_chars = client_serialized_char_len(rendered).value
        assert page_chars <= _MAX_FLOW_RECORD_SERIALIZED_CHARS, (
            f"implementation flow_records page {part}/{page_plan.total_parts} "
            f"serialized chars ({page_chars:,}) exceeds "
            f"{_MAX_FLOW_RECORD_SERIALIZED_CHARS:,} pin"
        )
