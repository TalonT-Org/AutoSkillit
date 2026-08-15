"""Consumed-field wire schema: inline payloads carry only fields with programmatic consumers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.config import OutputBudgetConfig
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS, compile_bounded_page_plan

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

# Delivery-pipeline fields: always present in every inline delivery,
# added by finalize_recipe_delivery.  Consumer: recipe initialization,
# execution, and pull machinery.
_DELIVERY_PIPELINE_FIELDS = frozenset(
    {
        "content",
        "content_hash",
        "composite_hash",
        "errors",
        "flow_records",
        "recipe_execution",
        "recipe_flow",
        "recipe_pull",
        "success",
    }
)

# Recipe payload fields: recipe-specific content consumed by the
# sous-chef and recipe system.  Vary by recipe; all are legitimate.
_RECIPE_PAYLOAD_FIELDS = frozenset(
    {
        "diagram",
        "ingredients_table",
        "initialization_id",
        "kitchen",
        "kitchen_rules",
        "orchestration_rules",
        "post_prune_routing_edges",
        "post_prune_step_names",
        "recipe_version",
        "requires_packs",
        "stop_step_semantics",
        "suggestions",
        "valid",
        "version",
    }
)

# The union of all fields with programmatic consumers.
_CONSUMED_INLINE_FIELDS = _DELIVERY_PIPELINE_FIELDS | _RECIPE_PAYLOAD_FIELDS

_SEGMENTED_INLINE_FIELDS = frozenset(
    {
        "composite_hash",
        "content_hash",
        "errors",
        "initialization_id",
        "kitchen",
        "recipe_flow",
        "recipe_pull",
        "recipe_segment",
        "recipe_version",
        "requires_packs",
        "success",
        "summary",
        "valid",
        "version",
    }
)

# Fields that MUST NOT appear on the wire: dead weight with no programmatic consumers.
_EXCLUDED_INLINE_FIELDS = frozenset(
    {
        "finalized_recipe_projection",
    }
)

# Fields that may appear when handler-injected (e.g. open_kitchen injects
# "warnings" for override warnings) but are not part of the delivery
# pipeline's own output.  Exact-equality checks must allow these.
_HANDLER_INJECTED_FIELDS = frozenset(
    {
        "warnings",
    }
)


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_inline_payload_schema_is_exact(
    recipe_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline payloads carry exactly the consumed fields, no more."""
    envelope = compile_bounded_page_plan(
        recipe_path,
        "open_kitchen",
        "claude-code",
        temp_dir=tmp_path,
        monkeypatch=monkeypatch,
        output_budget=OutputBudgetConfig(),
    )
    if envelope.get("delivery_bound_spill") is True:
        pytest.skip("resolves ENVELOPE — no inline fields to check")

    payload_fields = set(envelope)
    if "recipe_segment" in envelope:
        assert payload_fields == _SEGMENTED_INLINE_FIELDS
        carrier = envelope["recipe_segment"]
        assert carrier["kind"] == "startup"
        bodies = carrier["bodies"]
        assert isinstance(bodies, list) and bodies
        assert len(json.dumps(envelope, separators=(",", ":")).encode("utf-8")) < 10_000
        return
    present_excluded = _EXCLUDED_INLINE_FIELDS & payload_fields
    assert not present_excluded, f"Excluded fields in inline payload: {sorted(present_excluded)}"

    # Every consumed field must be present
    missing_consumed = _CONSUMED_INLINE_FIELDS - payload_fields
    assert not missing_consumed, (
        f"Missing consumed fields for {recipe_path.stem}: {sorted(missing_consumed)}"
    )

    # Exact equality: no unexpected fields beyond consumed + handler-injected.
    # A future field addition must either name its consumer in
    # _CONSUMED_INLINE_FIELDS or be explicitly handler-injected.
    unexpected = payload_fields - _CONSUMED_INLINE_FIELDS - _HANDLER_INJECTED_FIELDS
    assert not unexpected, (
        f"Unexpected wire fields for {recipe_path.stem}: {sorted(unexpected)}. "
        "Add consumed fields to _CONSUMED_INLINE_FIELDS (with consumer citation) "
        "or handler-injected fields to _HANDLER_INJECTED_FIELDS."
    )
