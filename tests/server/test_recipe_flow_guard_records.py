"""Flow-record and delivery coverage for finalized recipe step guards."""

from __future__ import annotations

import hashlib
import json

import pytest

from autoskillit.core import (
    RECIPE_FLOW_SCHEMA_VERSION,
    FinalizedRecipeProjection,
    RecipeBindingProjection,
    RecipeFlowGeneration,
    RecipeStepGuard,
)
from autoskillit.server._recipe_artifact import build_recipe_flow_generation
from tests.server.test_tools_recipe_pull import _finalize_recipe_delivery, _payload

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _projection(*, guarded: bool) -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection({}),
        ordered_step_names=("apply", "synthesize"),
        entrypoint="apply",
        ordered_flow_edges=(),
        ordered_step_guards=(
            (RecipeStepGuard("apply", "is_silent_type", "synthesize"),) if guarded else ()
        ),
    )


def _expected_digest(records: tuple[str, ...]) -> str:
    encoded = b"".join(
        len(record.encode("utf-8")).to_bytes(8, "big") + record.encode("utf-8")
        for record in records
    )
    return "sha256:" + hashlib.sha256(b"autoskillit.recipe-flow.v2\0" + encoded).hexdigest()


def test_build_recipe_flow_generation_records_guard_and_preserves_unguarded_shape() -> None:
    guarded = build_recipe_flow_generation(_projection(guarded=True))
    unguarded = build_recipe_flow_generation(_projection(guarded=False))

    assert [json.loads(record) for record in guarded.records] == [
        {"kind": "entrypoint", "name": "apply"},
        {
            "guard": {"bypass": "synthesize", "context": "is_silent_type"},
            "index": 0,
            "kind": "step",
            "name": "apply",
        },
        {"index": 1, "kind": "step", "name": "synthesize"},
    ]
    assert unguarded.records[1] == '{"index":0,"kind":"step","name":"apply"}'
    assert unguarded.records[2] == '{"index":1,"kind":"step","name":"synthesize"}'
    assert guarded.schema_version == RECIPE_FLOW_SCHEMA_VERSION == 2
    assert guarded.flow_sha256 == _expected_digest(guarded.records)


def test_recipe_flow_generation_rejects_the_previous_schema_version() -> None:
    with pytest.raises(ValueError, match="unsupported recipe flow schema version"):
        RecipeFlowGeneration(schema_version=1, records=('{"kind":"entrypoint","name":"apply"}',))


def test_finalize_recipe_delivery_serves_guarded_flow_record(
    tool_ctx_kitchen_open,
) -> None:
    finalized = _finalize_recipe_delivery(
        _payload(),
        surface="get_recipe",
        recipe_name="guarded",
        tool_ctx=tool_ctx_kitchen_open,
        finalized_projection=_projection(guarded=True),
    )

    body = json.loads(finalized.rendered)
    records = [json.loads(record) for record in body["flow_records"]]
    assert records[1]["guard"] == {"bypass": "synthesize", "context": "is_silent_type"}
