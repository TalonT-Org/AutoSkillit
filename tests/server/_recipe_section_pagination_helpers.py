"""Shared module-level helpers for recipe-section pagination tests.

Exposes the same private helper names as the pre-split
``test_recipe_section_pagination.py`` so the new focused test files can
import them under their original names and keep every test body verbatim.
"""

from __future__ import annotations

import json
from typing import Any

from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    RecipeArtifactGeneration,
    recipe_section_element_digest,
)
from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server._recipe_section_pagination import (
    build_recipe_section_page_plan,
    render_recipe_section_page,
    select_recipe_section,
)

_PAGE_TEST_BOUND = 2_000

_ALL_RANGE_FIELDS = {
    "byte_start",
    "byte_end",
    "byte_total",
    "element_start",
    "element_end",
    "element_total",
    "scalar_byte_start",
    "scalar_byte_end",
    "scalar_byte_total",
    "element_index",
    "element_sha256",
    "fragment_index",
    "fragment_count",
    "fragment_byte_start",
    "fragment_byte_end",
    "fragment_byte_total",
}

_RANGE_FIELDS_BY_FORMAT = {
    "raw-text": {"byte_start", "byte_end", "byte_total"},
    "json-array-page": {"element_start", "element_end", "element_total"},
    "json-scalar-page": {
        "scalar_byte_start",
        "scalar_byte_end",
        "scalar_byte_total",
    },
    "json-element-fragment": {
        "element_index",
        "element_sha256",
        "fragment_index",
        "fragment_count",
        "fragment_byte_start",
        "fragment_byte_end",
        "fragment_byte_total",
    },
}


def _generation(**changes: object) -> RecipeArtifactGeneration:
    """Build a RecipeArtifactGeneration with valid defaults."""
    base: dict[str, object] = {
        "producer_tool": "open_kitchen",
        "recipe_name": "remediation",
        "descriptor_version": RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        "schema_version": RECIPE_ARTIFACT_SCHEMA_VERSION,
        "payload_sha256": f"sha256:{'1' * 64}",
        "artifact_blob_sha256": f"sha256:{'2' * 64}",
        "artifact_blob_size_bytes": 4096,
        "body_sha256": f"sha256:{'3' * 64}",
        "body_size_bytes": 2048,
        "flow_schema_version": RECIPE_FLOW_SCHEMA_VERSION,
        "flow_sha256": f"sha256:{'4' * 64}",
        "flow_size_bytes": 512,
        "flow_record_count": 2,
    }
    base.update(changes)
    return RecipeArtifactGeneration(**base)  # type: ignore[arg-type]


def _payload(**changes: object) -> dict[str, object]:
    """Build a payload with all six sections defaulted."""
    payload: dict[str, object] = {
        "content": "name: remediation\nsteps:\n  first:\n    action: stop\n",
        "ingredients_table": "| ingredient | value |\n|---|---|\n| task | demo |\n",
        "orchestration_rules": "Follow the graph exactly.",
        "stop_step_semantics": "Stop means return immediately.",
        "errors": [],
        "warnings": [],
        "post_prune_step_names": ["first"],
    }
    payload.update(changes)
    return payload


def _build(
    payload: dict[str, object],
    section: str,
    *,
    bound: int,
    generation: RecipeArtifactGeneration | None = None,
    kitchen_id: str = "kitchen-test",
    dynamic_content: str | None = None,
) -> Any:
    """Select a section and build a page plan."""
    selected = select_recipe_section(
        payload,
        section,
        dynamic_content=dynamic_content,
    )
    return build_recipe_section_page_plan(
        kitchen_id=kitchen_id,
        generation=generation or _generation(),
        selected=selected,
        recipe_section_bound_bytes=bound,
    )


def _rendered_pages(plan: Any) -> list[str]:
    return [render_recipe_section_page(plan, part) for part in range(plan.total_parts)]


def _clear_page_plan_cache() -> None:
    cache = pagination._PAGE_PLAN_CACHE
    assert cache is not None
    cache.clear()


def _decoded_pages(plan: Any, *, bound: int) -> list[dict[str, Any]]:
    """Render and decode every page; assert identity invariants across pages."""
    rendered = _rendered_pages(plan)
    decoded: list[dict[str, Any]] = []
    for part, page_text in enumerate(rendered):
        assert len(page_text.encode("utf-8")) <= bound
        page = json.loads(page_text)
        assert page_text == json.dumps(
            page,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert page["success"] is True
        assert page["part"] == part
        assert page["total_parts"] == len(rendered)
        assert page["has_more"] is (part + 1 < len(rendered))
        if page["has_more"]:
            assert page["next_part"] == part + 1
        else:
            assert "next_part" not in page
        required_ranges = _RANGE_FIELDS_BY_FORMAT[page["content_format"]]
        assert required_ranges <= page.keys()
        assert not ((_ALL_RANGE_FIELDS - required_ranges) & page.keys())
        assert page["content"] != "" or page["content_format"] == "json-array-page"
        if page["content_format"] == "json-array-page":
            assert isinstance(page["content"], list)
        elif page["content_format"] != "raw-text":
            json.loads(page["content"])
        decoded.append(page)

    identities = {
        (
            page["pagination_version"],
            page["section_registry_sha256"],
            page["payload_sha256"],
            page["body_sha256"],
            page["page_plan_sha256"],
            page["section_sha256"],
        )
        for page in decoded
    }
    assert len(identities) == 1
    return decoded


def _reconstruct(pages: list[dict[str, Any]]) -> object:
    """Reassemble original content from decoded pages (raw, scalar, or array)."""
    formats = {page["content_format"] for page in pages}
    if formats == {"raw-text"}:
        cursor = 0
        chunks: list[str] = []
        for page in pages:
            assert page["byte_start"] == cursor
            assert page["byte_end"] > page["byte_start"]
            chunk = page["content"]
            assert len(chunk.encode("utf-8")) == page["byte_end"] - page["byte_start"]
            chunks.append(chunk)
            cursor = page["byte_end"]
        assert cursor == pages[-1]["byte_total"]
        return "".join(chunks)

    if formats == {"json-scalar-page"}:
        cursor = 0
        chunks = []
        for page in pages:
            assert page["scalar_byte_start"] == cursor
            assert page["scalar_byte_end"] > page["scalar_byte_start"]
            chunk = json.loads(page["content"])
            assert isinstance(chunk, str)
            assert (
                len(chunk.encode("utf-8")) == page["scalar_byte_end"] - page["scalar_byte_start"]
            )
            chunks.append(chunk)
            cursor = page["scalar_byte_end"]
        assert cursor == pages[-1]["scalar_byte_total"]
        return "".join(chunks)

    assert formats <= {"json-array-page", "json-element-fragment"}
    result: list[object] = []
    element_cursor = 0
    page_cursor = 0
    while page_cursor < len(pages):
        page = pages[page_cursor]
        if page["content_format"] == "json-array-page":
            assert page["element_start"] == element_cursor
            values = page["content"]
            assert isinstance(values, list) and values
            assert page["element_end"] - page["element_start"] == len(values)
            result.extend(values)
            element_cursor = page["element_end"]
            page_cursor += 1
            continue

        element_index = page["element_index"]
        assert element_index == element_cursor
        fragment_count = page["fragment_count"]
        fragments: list[str] = []
        fragment_byte_cursor = 0
        for expected_fragment in range(fragment_count):
            fragment_page = pages[page_cursor]
            assert fragment_page["content_format"] == "json-element-fragment"
            assert fragment_page["element_index"] == element_index
            assert fragment_page["fragment_index"] == expected_fragment
            assert fragment_page["fragment_count"] == fragment_count
            assert fragment_page["fragment_byte_start"] == fragment_byte_cursor
            fragment = json.loads(fragment_page["content"])
            assert isinstance(fragment, str) and fragment
            assert (
                len(fragment.encode("utf-8"))
                == fragment_page["fragment_byte_end"] - fragment_page["fragment_byte_start"]
            )
            fragments.append(fragment)
            fragment_byte_cursor = fragment_page["fragment_byte_end"]
            page_cursor += 1
        assert fragment_byte_cursor == page["fragment_byte_total"]
        canonical_element = "".join(fragments)
        assert page["element_sha256"] == recipe_section_element_digest(
            json.loads(canonical_element)
        )
        result.append(json.loads(canonical_element))
        element_cursor += 1

    assert element_cursor == pages[-1].get("element_total", element_cursor)
    return result
