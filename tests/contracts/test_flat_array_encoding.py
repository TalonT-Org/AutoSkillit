"""Wire-shape contract: array-section content is a structured list, not a string."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    RECIPE_SECTION_REGISTRY,
    RecipeArtifactGeneration,
)
from autoskillit.server._recipe_section_pagination import (
    build_recipe_section_page_plan,
    render_recipe_section_page,
    select_recipe_section,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_ARRAY_SECTIONS = [
    name
    for name, defn in RECIPE_SECTION_REGISTRY.items()
    if defn.ordinary_content_format == "json-array-page"
]
_SCALAR_OR_RAW_SECTIONS = [
    name
    for name, defn in RECIPE_SECTION_REGISTRY.items()
    if defn.ordinary_content_format != "json-array-page"
]


def _generation() -> RecipeArtifactGeneration:
    return RecipeArtifactGeneration(
        producer_tool="open_kitchen",
        recipe_name="remediation",
        descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
        payload_sha256=f"sha256:{'1' * 64}",
        artifact_blob_sha256=f"sha256:{'2' * 64}",
        artifact_blob_size_bytes=4096,
        body_sha256=f"sha256:{'3' * 64}",
        body_size_bytes=2048,
        flow_schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        flow_sha256=f"sha256:{'4' * 64}",
        flow_size_bytes=512,
        flow_record_count=2,
    )


def _payload() -> dict[str, object]:
    return {
        "content": "name: remediation\nsteps:\n  first:\n    action: stop\n",
        "ingredients_table": "| ingredient | value |\n|---|---|\n| task | demo |\n",
        "orchestration_rules": "Follow the graph exactly.",
        "stop_step_semantics": "Stop means return immediately.",
        "errors": ["first error", "second error"],
        "flow_records": ["record-one", "record-two"],
        "warnings": ["first warning", "second warning"],
        "post_prune_step_names": ["first"],
    }


def test_registry_identifies_the_three_array_sections() -> None:
    assert set(_ARRAY_SECTIONS) == {"errors", "flow_records", "warnings"}


@pytest.mark.parametrize("section", _ARRAY_SECTIONS)
def test_array_section_content_is_structured_not_string(section: str) -> None:
    """Complete json-array-page content must be a parsed list, not a JSON string."""
    payload = _payload()
    selected = select_recipe_section(payload, section)
    plan = build_recipe_section_page_plan(
        kitchen_id="contract-flat-array",
        generation=_generation(),
        selected=selected,
        recipe_section_bound_bytes=8_192,
    )
    rendered = json.loads(render_recipe_section_page(plan, 0))

    assert rendered["content_format"] == "json-array-page"
    assert isinstance(rendered["content"], list)
    assert rendered["content"] == payload[section]


@pytest.mark.parametrize("section", _SCALAR_OR_RAW_SECTIONS)
def test_non_array_section_content_remains_a_string(section: str) -> None:
    """raw-text and json-scalar-page content is untouched by array flattening."""
    payload = _payload()
    selected = select_recipe_section(payload, section)
    plan = build_recipe_section_page_plan(
        kitchen_id="contract-flat-array",
        generation=_generation(),
        selected=selected,
        recipe_section_bound_bytes=8_192,
    )
    rendered = json.loads(render_recipe_section_page(plan, 0))

    assert rendered["content_format"] != "json-array-page"
    assert isinstance(rendered["content"], str)
