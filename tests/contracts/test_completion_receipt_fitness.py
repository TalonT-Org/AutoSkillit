"""Build-time fitness: every bundled recipe's real completion receipt fits every bound.

Reuses the bound enumeration from ``test_delivery_bound_fitness`` and builds the receipt through
the production renderer with a real execution credential — no synthetic digests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    build_recipe_execution_credential,
    resolve_general_output_token_limit,
)
from autoskillit.server._recipe_delivery import (
    persist_recipe_artifact,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_generation import RecipeGenerationStore
from autoskillit.server._recipe_initialization import (
    _render_completion_receipt,
    recipe_initialization_receipt,
)
from tests.contracts.test_delivery_bound_fitness import (
    _backend_capabilities,
    _delivery_recipe_names,
    _full_open_kitchen_generation,
    _generic_backstop_bound_bytes,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_implementation_plan_shape_supplies_unavailable_audit_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.recipe._binding import bind_runtime_skill_invocation
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    monkeypatch.setattr(_recipe_generation, "_RECIPE_GENERATION_STORE", RecipeGenerationStore())
    payload, projection = _full_open_kitchen_generation("implementation")
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=None,
            kitchen_id="implementation-plan-shape",
            temp_dir=tmp_path,
        ),
    )
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name="implementation",
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    credential = build_recipe_execution_credential(prepared.execution_snapshot)
    shape = credential.as_wire_block()["skill_input_shapes"]["plan"]
    assert shape == {
        "keys": ["task", "issue_url", "adversarial_review_level", "audit_cycle_path"],
        "unresolved_defaults": {"audit_cycle_path": ""},
    }

    resolved = {
        "task": "test task",
        "issue_url": "https://github.com/test/test/issues/1",
        "adversarial_review_level": "standard",
    }
    defaults = cast(dict[str, str | int | bool], shape["unresolved_defaults"])
    assembled = {
        name: resolved[name] if name in resolved else defaults[name]
        for name in cast(list[str], shape["keys"])
    }
    template = prepared.execution_snapshot.templates["plan"]
    actual_mcp_kwargs = {
        value.name: value.effective_value
        for value in template.invocation.mcp_kwargs
        if isinstance(value.effective_value, (str, int, bool))
    }

    bound = bind_runtime_skill_invocation(
        template,
        execution_id=prepared.execution_snapshot.execution_id,
        step_name="plan",
        skill_command="/autoskillit:make-plan",
        skill_inputs=assembled,
        actual_mcp_kwargs=actual_mcp_kwargs,
    )

    assert dict(bound) == assembled


@pytest.mark.parametrize("recipe_name", _delivery_recipe_names(), ids=lambda n: n)
def test_completion_receipt_fits_every_delivery_bound(
    recipe_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    monkeypatch.setattr(_recipe_generation, "_RECIPE_GENERATION_STORE", RecipeGenerationStore())

    payload, projection = _full_open_kitchen_generation(recipe_name)
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=None,
            kitchen_id=f"receipt-fitness-{recipe_name}",
            temp_dir=tmp_path,
        ),
    )
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name=recipe_name,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    generation = persist_recipe_artifact(
        tmp_path,
        kitchen_id=tool_ctx.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name=recipe_name,
        payload=prepared.canonical_artifact_payload,
        flow_generation=prepared.flow_generation,
    )
    initialization_id = f"fitness-{recipe_name}"
    credential = build_recipe_execution_credential(prepared.execution_snapshot)
    receipt = _render_completion_receipt(
        initialization_id=initialization_id,
        completion_receipt=recipe_initialization_receipt(initialization_id, generation),
        recipe_name=recipe_name,
        artifact_generation=generation,
        flow_generation=prepared.flow_generation,
        credential=credential,
    )
    receipt_bytes = len(receipt.encode("utf-8"))

    response_max_bytes = OutputBudgetConfig().response_max_bytes
    assert receipt_bytes <= response_max_bytes, (
        f"{recipe_name}: completion receipt is {receipt_bytes} bytes, "
        f"exceeds response_max_bytes={response_max_bytes}"
    )
    for backend_name, capabilities in _backend_capabilities().items():
        bound_bytes = _generic_backstop_bound_bytes(
            resolve_general_output_token_limit(capabilities)
        )
        assert receipt_bytes <= bound_bytes, (
            f"{backend_name}/{recipe_name}: completion receipt is {receipt_bytes} bytes, "
            f"exceeds the backend general output limit of {bound_bytes} bytes"
        )
    measured = json.loads(receipt)[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
    assert set(measured) == set(credential.as_wire_block())
    assert measured["execution_id"] == prepared.execution_snapshot.execution_id
    assert measured["invocation_template_digests"] == dict(
        prepared.execution_snapshot.template_digests
    ), f"{recipe_name}: the measured receipt must carry the real credential"
    assert measured["skill_input_shapes"] == credential.as_wire_block()["skill_input_shapes"]
