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


@pytest.mark.parametrize("section", _ARRAY_SECTIONS)
def test_complete_array_page_has_no_nested_json_strings(section: str) -> None:
    """Complete json-array-page body fields must not contain JSON-parseable
    structured strings — the anti-nesting property.
    """
    payload = _payload()
    selected = select_recipe_section(payload, section)
    plan = build_recipe_section_page_plan(
        kitchen_id="contract-flat-array-nesting",
        generation=_generation(),
        selected=selected,
        recipe_section_bound_bytes=8_192,
    )
    rendered = json.loads(render_recipe_section_page(plan, 0))

    # content must be a list (already tested), but also verify no element
    # in the body is a string that parses to a dict or list (nested JSON)
    for key, value in rendered.items():
        if key == "content":
            # content elements may be strings (element_kind="string" for
            # errors/warnings) but must not be structured JSON strings
            assert isinstance(value, list)
            for element in value:
                if isinstance(element, str):
                    try:
                        parsed = json.loads(element)
                    except (json.JSONDecodeError, TypeError):
                        continue  # not parseable — fine
                    assert not isinstance(parsed, dict | list), (
                        f"{section} content element is a JSON-structured string: {element[:80]!r}"
                    )
        elif isinstance(value, str) and key not in (
            "continuation",
            "section_sha256",
            "page_plan_sha256",
            "pagination_policy_sha256",
            "section_registry_sha256",
            "content_format",
            "section",
            "producer_tool",
            "recipe_name",
            "payload_sha256",
            "artifact_blob_sha256",
            "body_sha256",
            "flow_sha256",
            "initialization_id",
            "page_content_sha256",
        ):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue
            assert not isinstance(parsed, dict | list), (
                f"{section} body field {key!r} is a JSON-structured string"
            )


def test_fragment_pages_preserve_string_content() -> None:
    """json-element-fragment continuation pages must carry string content,
    not parsed lists — partial JSON cannot be structured.
    """
    # Create a payload with an oversized flow_records element that forces
    # fragmentation at a small bound
    large_record = json.dumps({"kind": "step", "name": "x" * 500, "index": 0})
    payload = _payload()
    payload["flow_records"] = [large_record]

    selected = select_recipe_section(dict(payload), "flow_records")
    plan = build_recipe_section_page_plan(
        kitchen_id="contract-fragment",
        generation=_generation(),
        selected=selected,
        recipe_section_bound_bytes=300,  # force fragmentation
    )
    # If the element fits in one page, skip (fragmentation not triggered)
    if plan.total_parts <= 1:
        pytest.skip("element fits in one page — fragmentation not triggered")
    for part in range(plan.total_parts):
        rendered = json.loads(render_recipe_section_page(plan, part))
        if rendered.get("content_format") == "json-element-fragment":
            # Fragment content must be a string (partial JSON)
            assert isinstance(rendered["content"], str), (
                f"Fragment page {part} content is {type(rendered['content']).__name__}, "
                "expected str"
            )
