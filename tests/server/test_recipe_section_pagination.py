"""Pure recipe-section planner, renderer, reconstruction, and cache contracts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from typing import Any

import pytest

from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    RECIPE_SECTION_MANDATORY_FAILURE_CODES,
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
    RecipeArtifactGeneration,
    client_serialized_char_len,
    recipe_section_digest,
    recipe_section_element_digest,
    recipe_section_plan_digest,
)
from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server import _recipe_section_planning as planning
from autoskillit.server._recipe_initialization import recipe_initialization_receipt
from autoskillit.server._recipe_section_pagination import (
    PagePlanCache,
    RecipeSectionBoundError,
    RecipeSectionPageDescriptor,
    RecipeSectionPaginationError,
    RecipeSectionRequestState,
    build_recipe_section_page_plan,
    extract_recipe_step_bodies,
    get_or_build_recipe_section_page_plan,
    render_recipe_section_failure,
    render_recipe_section_page,
    select_recipe_section,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

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


def test_extract_recipe_step_bodies_preserves_requested_order() -> None:
    persisted = {
        "content": """name: segmented
steps:
  first:
    tool: run_cmd
    with:
      cmd: echo first
  second:
    action: stop
    message: done
"""
    }
    bodies = extract_recipe_step_bodies(persisted, ("second", "first"))
    assert tuple(step_name for step_name, _body in bodies) == ("second", "first")
    assert bodies[0][1].startswith("second:")
    assert bodies[1][1].startswith("first:")


_PAGE_TEST_BOUND = 2_000


@pytest.fixture(autouse=True)
def _fresh_page_plan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", PagePlanCache())


def _generation(**changes: object) -> RecipeArtifactGeneration:
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


@pytest.mark.parametrize(
    "bound",
    [True, 1.0, 0, RECIPE_SECTION_RESPONSE_FLOOR_BYTES - 1],
)
def test_request_state_rejects_non_integer_and_below_floor_bounds(bound: object) -> None:
    with pytest.raises(ValueError, match="bound must be an integer at least"):
        RecipeSectionRequestState(
            admitted=True,
            recipe_section_bound_bytes=bound,  # type: ignore[arg-type]
        )


def test_page_descriptor_rejects_unknown_incomplete_and_mixed_range_families() -> None:
    digest = f"sha256:{'0' * 64}"
    with pytest.raises(ValueError, match="unknown recipe section content format"):
        RecipeSectionPageDescriptor(
            content_format="unknown",  # type: ignore[arg-type]
            page_content_sha256=digest,
        )
    with pytest.raises(ValueError, match="range fields must exactly match"):
        RecipeSectionPageDescriptor(
            content_format="raw-text",
            page_content_sha256=digest,
            byte_start=0,
            byte_end=1,
        )
    with pytest.raises(ValueError, match="range fields must exactly match"):
        RecipeSectionPageDescriptor(
            content_format="raw-text",
            page_content_sha256=digest,
            byte_start=0,
            byte_end=1,
            byte_total=1,
            scalar_byte_start=0,
            scalar_byte_end=1,
            scalar_byte_total=1,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"page_content_sha256": "sha256:not-a-digest"}, "page content digest"),
        ({"byte_start": False}, "byte_start must be a non-negative integer"),
        ({"byte_end": 1.0}, "byte_end must be a non-negative integer"),
        ({"byte_start": -1}, "byte_start must be a non-negative integer"),
        (
            {"byte_start": 2, "byte_end": 1},
            "range must make ordered progress within its total",
        ),
        (
            {"byte_start": 1, "byte_end": 1, "byte_total": 2},
            "range must make ordered progress within its total",
        ),
        (
            {"byte_end": 3, "byte_total": 2},
            "range must make ordered progress within its total",
        ),
    ],
)
def test_page_descriptor_rejects_malformed_raw_range_values(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "content_format": "raw-text",
        "page_content_sha256": f"sha256:{'0' * 64}",
        "byte_start": 0,
        "byte_end": 1,
        "byte_total": 2,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        RecipeSectionPageDescriptor(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"element_sha256": "sha256:BAD"}, "element_sha256"),
        ({"element_index": False}, "element_index must be a non-negative integer"),
        ({"fragment_count": 0}, "within a positive fragment count"),
        ({"fragment_index": 2}, "within a positive fragment count"),
    ],
)
def test_page_descriptor_rejects_malformed_fragment_values(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "content_format": "json-element-fragment",
        "page_content_sha256": f"sha256:{'0' * 64}",
        "element_index": 0,
        "element_sha256": f"sha256:{'1' * 64}",
        "fragment_index": 0,
        "fragment_count": 2,
        "fragment_byte_start": 0,
        "fragment_byte_end": 1,
        "fragment_byte_total": 2,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        RecipeSectionPageDescriptor(**values)  # type: ignore[arg-type]


def test_scalar_planning_never_serializes_the_whole_oversized_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = planning.canonical_recipe_section_json
    serialized_string_bytes: list[int] = []

    def _record_bounded_string(value: object) -> str:
        if type(value) is str:
            serialized_string_bytes.append(len(value.encode("utf-8")))
        return original(value)

    monkeypatch.setattr(planning, "canonical_recipe_section_json", _record_bounded_string)
    bound = _PAGE_TEST_BOUND
    plan = _build(
        _payload(ingredients_table="x" * 10_000),
        "ingredients_table",
        bound=bound,
    )

    assert plan.total_parts > 1
    assert serialized_string_bytes
    assert max(serialized_string_bytes) <= bound


def _payload(**changes: object) -> dict[str, object]:
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


def test_terminal_initialization_page_carries_progress_and_completion_receipt() -> None:
    generation = _generation()
    selected = dataclasses.replace(
        select_recipe_section(_payload(), "content"),
        initialization_id="initialization",
        completion_response={
            "completion_receipt": f"sha256:{'a' * 64}",
            "recipe_execution": {"execution_id": "execution"},
        },
    )
    plan = build_recipe_section_page_plan(
        kitchen_id="kitchen-test",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=90_000,
    )

    page = json.loads(plan.rendered_pages[-1])
    assert page["completed_parts"] == page["total_parts"]
    assert page["remaining_section_pulls"] == 0
    content_sha256 = page["content_sha256"]
    assert page["completion_receipt"] == recipe_initialization_receipt(
        "initialization",
        generation,
        content_sha256=content_sha256,
    )
    assert page["recipe_execution"] == {"execution_id": "execution"}


def test_char_ceiling_accepts_a_page_within_it() -> None:
    """A char_ceiling at least as large as the exact client-serialized length
    of every rendered page does not disturb an otherwise-fitting plan."""
    plan = _build(
        _payload(orchestration_rules="follow the graph exactly"),
        "orchestration_rules",
        bound=_PAGE_TEST_BOUND,
    )
    exact_char_ceiling = max(
        client_serialized_char_len(rendered).value for rendered in plan.rendered_pages
    )

    replan = build_recipe_section_page_plan(
        kitchen_id="kitchen-test",
        generation=_generation(),
        selected=select_recipe_section(
            _payload(orchestration_rules="follow the graph exactly"), "orchestration_rules"
        ),
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
        char_ceiling=exact_char_ceiling,
    )
    assert replan.rendered_pages == plan.rendered_pages


def test_char_ceiling_rejects_a_page_that_exceeds_it() -> None:
    """A char_ceiling one below the exact client-serialized length of a
    rendered page must fail planning even though every page satisfies
    the byte bound — the client gates on serialized chars, not bytes.

    The dual-domain planner enforces both bounds during page fitting
    (not just post-hoc verification), so a candidate that exceeds the
    char ceiling is rejected at the same "cannot fit progress" boundary
    as a byte-bound violation — it never reaches a page that would need
    the final-form verification check to reject it.
    """
    payload = _payload(orchestration_rules="follow the graph exactly")
    plan = _build(payload, "orchestration_rules", bound=_PAGE_TEST_BOUND)
    exact_char_ceiling = max(
        client_serialized_char_len(rendered).value for rendered in plan.rendered_pages
    )

    with pytest.raises(RecipeSectionBoundError, match="cannot fit progress"):
        build_recipe_section_page_plan(
            kitchen_id="kitchen-test",
            generation=_generation(),
            selected=select_recipe_section(payload, "orchestration_rules"),
            recipe_section_bound_bytes=_PAGE_TEST_BOUND,
            char_ceiling=exact_char_ceiling - 1,
        )


def test_select_recipe_section_loads_only_recognized_dynamic_content() -> None:
    loaded_sections: list[str] = []

    def _load_dynamic_content(section: str) -> str:
        loaded_sections.append(section)
        return "first:\n  action: stop\n"

    payload = _payload()

    fixed = select_recipe_section(
        payload,
        "content",
        dynamic_content_loader=_load_dynamic_content,
    )
    dynamic = select_recipe_section(
        payload,
        "first",
        dynamic_content_loader=_load_dynamic_content,
    )
    unknown = select_recipe_section(
        payload,
        "unknown",
        dynamic_content_loader=_load_dynamic_content,
    )

    assert fixed.present is True
    assert dynamic.present is True
    assert dynamic.value == "first:\n  action: stop\n"
    assert unknown.present is False
    assert loaded_sections == ["first"]


def test_select_recipe_section_rejects_empty_dynamic_content() -> None:
    selected = select_recipe_section(
        _payload(),
        "first",
        dynamic_content_loader=lambda _section: "",
    )

    assert selected.present is False
    assert selected.value == ""


def test_failure_floor_is_derived_from_the_registered_renderer() -> None:
    assert (
        render_recipe_section_failure.__module__ == "autoskillit.server.recipe_section._rendering"
    )
    rendered_failures = [
        render_recipe_section_failure(
            code,
            bound_bytes=RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
        )
        for code in RECIPE_SECTION_MANDATORY_FAILURE_CODES
    ]

    assert max(len(rendered.encode("utf-8")) for rendered in rendered_failures) == (
        RECIPE_SECTION_RESPONSE_FLOOR_BYTES
    )
    with pytest.raises(ValueError, match="unregistered recipe section failure code"):
        render_recipe_section_failure(
            "unregistered_failure",
            bound_bytes=RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
        )


def _rendered_pages(plan: Any) -> list[str]:
    return [render_recipe_section_page(plan, part) for part in range(plan.total_parts)]


def _clear_page_plan_cache() -> None:
    cache = pagination._PAGE_PLAN_CACHE
    assert cache is not None
    cache.clear()


def _decoded_pages(plan: Any, *, bound: int) -> list[dict[str, Any]]:
    rendered_pages = _rendered_pages(plan)
    decoded_pages: list[dict[str, Any]] = []
    for part, rendered in enumerate(rendered_pages):
        assert len(rendered.encode("utf-8")) <= bound
        page = json.loads(rendered)
        assert rendered == json.dumps(
            page,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert page["success"] is True
        assert page["part"] == part
        assert page["total_parts"] == len(rendered_pages)
        assert page["has_more"] is (part + 1 < len(rendered_pages))
        if page["has_more"]:
            assert page["next_part"] == part + 1
        else:
            assert "next_part" not in page
        required_ranges = _RANGE_FIELDS_BY_FORMAT[page["content_format"]]
        assert required_ranges <= page.keys()
        assert not ((_ALL_RANGE_FIELDS - required_ranges) & page.keys())
        assert page["content"] != "" or page["content_format"] == "json-array-page"
        if page["content_format"] == "json-array-page":
            # Flat delivery encoding: array-page content arrives pre-parsed.
            assert isinstance(page["content"], list)
        elif page["content_format"] != "raw-text":
            json.loads(page["content"])
        decoded_pages.append(page)

    identities = {
        (
            page["pagination_version"],
            page["section_registry_sha256"],
            page["payload_sha256"],
            page["body_sha256"],
            page["page_plan_sha256"],
            page["section_sha256"],
        )
        for page in decoded_pages
    }
    assert len(identities) == 1
    return decoded_pages


def _reconstruct(pages: list[dict[str, Any]]) -> object:
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
            values = page["content"]  # already parsed by flat delivery encoding
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


@pytest.mark.parametrize(
    "value",
    [
        "plain ASCII text " * 100,
        "snowman ☃ and emoji 🥘 " * 100,
        'quotes " and backslashes \\\\ ' * 100,
        "tabs\tnewlines\ncarriage\rcontrols\u0001 " * 100,
    ],
)
def test_raw_pages_preserve_text_and_exact_utf8_bounds(value: str) -> None:
    bound = _PAGE_TEST_BOUND
    plan = _build(_payload(content=value), "content", bound=bound)
    pages = _decoded_pages(plan, bound=bound)

    assert _reconstruct(pages) == value
    assert pages[0]["section"] == "content"
    assert pages[0]["section_sha256"] == recipe_section_digest(value, raw=True)


@pytest.mark.parametrize(
    "value",
    [
        "| a | b |\n|---|---|\n| 1 | 2 |\n" * 60,
        'Markdown "quoted" \\\\ escaped ☃\n' * 80,
    ],
)
def test_json_scalar_pages_are_independently_valid_and_reconstruct_markdown(
    value: str,
) -> None:
    bound = _PAGE_TEST_BOUND
    plan = _build(_payload(ingredients_table=value), "ingredients_table", bound=bound)
    pages = _decoded_pages(plan, bound=bound)

    assert {page["content_format"] for page in pages} == {"json-scalar-page"}
    assert _reconstruct(pages) == value
    assert pages[0]["section_sha256"] == recipe_section_digest(value, raw=False)


def test_ordered_array_pages_are_complete_json_documents() -> None:
    values = [f"warning-{index:03d}-☃" * 4 for index in range(80)]
    bound = _PAGE_TEST_BOUND
    plan = _build(_payload(warnings=values), "warnings", bound=bound)
    pages = _decoded_pages(plan, bound=bound)

    assert {page["content_format"] for page in pages} == {"json-array-page"}
    assert _reconstruct(pages) == values
    assert pages[0]["section_sha256"] == recipe_section_digest(values, raw=False)


@pytest.mark.parametrize("oversized_index", [0, 1, 2])
def test_oversized_array_elements_fragment_in_first_middle_and_final_positions(
    oversized_index: int,
) -> None:
    values = ["before", "middle", "after"]
    values[oversized_index] = 'oversized-"quoted"-\\\\-☃-' * 600
    bound = _PAGE_TEST_BOUND
    plan = _build(_payload(warnings=values), "warnings", bound=bound)
    pages = _decoded_pages(plan, bound=bound)

    assert "json-element-fragment" in {page["content_format"] for page in pages}
    assert _reconstruct(pages) == values
    fragments = [page for page in pages if page["content_format"] == "json-element-fragment"]
    assert {page["element_index"] for page in fragments} == {oversized_index}
    assert {page["element_sha256"] for page in fragments} == {
        recipe_section_element_digest(values[oversized_index])
    }


def test_array_plan_can_interleave_ordinary_and_fragment_pages() -> None:
    values = [
        "ordinary-first",
        "x" * 12_000,
        "ordinary-middle",
        "y" * 12_000,
        "ordinary-final",
    ]
    bound = _PAGE_TEST_BOUND
    plan = _build(_payload(errors=values), "errors", bound=bound)
    pages = _decoded_pages(plan, bound=bound)

    formats = [page["content_format"] for page in pages]
    assert "json-array-page" in formats
    assert "json-element-fragment" in formats
    assert _reconstruct(pages) == values


def test_raw_recipe_and_named_step_yaml_use_unchanged_raw_reconstruction() -> None:
    recipe = "name: demo\nsteps:\n  first:\n    run: echo unchanged\n"
    named_step = "first:\n  run: echo unchanged\n"
    bound = _PAGE_TEST_BOUND

    recipe_plan = _build(_payload(content=recipe), "content", bound=bound)
    step_plan = _build(
        _payload(content=recipe),
        "first",
        bound=bound,
        dynamic_content=named_step,
    )

    assert _reconstruct(_decoded_pages(recipe_plan, bound=bound)) == recipe
    step_pages = _decoded_pages(step_plan, bound=bound)
    assert {page["content_format"] for page in step_pages} == {"raw-text"}
    assert _reconstruct(step_pages) == named_step


def test_exact_fit_succeeds_and_one_byte_under_replans_without_oversize() -> None:
    value = "exact-fit-☃-" * 300
    wide = _build(_payload(content=value), "content", bound=10_000)
    assert wide.total_parts == 1
    exact_bound = len(render_recipe_section_page(wide, 0).encode("utf-8"))

    exact = _build(_payload(content=value), "content", bound=exact_bound)
    assert exact.total_parts == 1
    assert len(render_recipe_section_page(exact, 0).encode("utf-8")) == exact_bound

    tight = _build(_payload(content=value), "content", bound=exact_bound - 1)
    assert tight.total_parts > 1
    assert _reconstruct(_decoded_pages(tight, bound=exact_bound - 1)) == value


def test_production_like_ten_thousand_byte_bound_is_honored() -> None:
    values = [f"warning-{index}-" + ("x" * 1_000) for index in range(50)]
    plan = _build(_payload(warnings=values), "warnings", bound=10_000)
    pages = _decoded_pages(plan, bound=10_000)

    assert len(pages) > 1
    assert _reconstruct(pages) == values


@pytest.mark.parametrize("strategy", ["raw", "scalar", "array", "fragment"])
def test_candidate_sizing_uses_binary_search_scale_oracle_calls(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    if strategy == "raw":
        payload = _payload(content="r" * 50_000)
        section = "content"
        search_space = 50_000
    elif strategy == "scalar":
        payload = _payload(ingredients_table="s" * 50_000)
        section = "ingredients_table"
        search_space = 50_000
    elif strategy == "array":
        values = [f"value-{index:04d}-" + ("a" * 40) for index in range(2_000)]
        payload = _payload(warnings=values)
        section = "warnings"
        search_space = len(values)
    else:
        fragment = "f" * 50_000
        payload = _payload(warnings=[fragment])
        section = "warnings"
        search_space = len(fragment)

    oracle_calls = 0
    original_fits = planning._fits

    def _counted_fits(**kwargs: Any) -> bool:
        nonlocal oracle_calls
        oracle_calls += 1
        return original_fits(**kwargs)

    monkeypatch.setattr(planning, "_fits", _counted_fits)

    plan = _build(payload, section, bound=_PAGE_TEST_BOUND)

    binary_search_scale = math.ceil(math.log2(search_space + 1))
    assert oracle_calls <= 6 * plan.total_parts * (binary_search_scale + 2)
    if strategy == "fragment":
        assert {json.loads(page)["content_format"] for page in plan.rendered_pages} == {
            "json-element-fragment"
        }


def test_convergence_ceiling_is_derived_from_artifact_policy() -> None:
    max_count_digits = len(str(RECIPE_ARTIFACT_MAX_BLOB_BYTES))

    assert pagination._convergence_iteration_ceiling() == 1 + (
        (RECIPE_ARTIFACT_MAX_BLOB_BYTES + 1) * (max_count_digits - 1)
    )
    assert pagination._convergence_iteration_ceiling() > 32


def test_final_digest_injection_revalidates_descriptor_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_render = planning._render_candidate

    def _corrupt_final_boundary(**kwargs: Any) -> str:
        rendered = original_render(**kwargs)
        if kwargs["page_plan_sha256"] != planning._PLAN_DIGEST_PLACEHOLDER and kwargs["part"] == 1:
            response = json.loads(rendered)
            response["byte_start"] += 1
            return json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        return rendered

    monkeypatch.setattr(pagination, "_render_candidate", _corrupt_final_boundary)

    with pytest.raises(
        RecipeSectionPaginationError,
        match="changed plan identity or boundaries",
    ):
        _build(_payload(content="boundary-check-" * 500), "content", bound=_PAGE_TEST_BOUND)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("fragment_count", "fragment identity changed"),
        ("element_digest", "fragment identity changed"),
        ("fragment_range", "fragment range does not match its content"),
        ("reconstruction", "do not reconstruct their element"),
        ("page_content_digest", "page content digest changed"),
    ],
)
def test_final_verifier_rejects_fragment_descriptor_and_content_corruption(
    mutation: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = pagination.verify_finalized_recipe_section_plan

    def _verify_corrupted(**kwargs: Any) -> None:
        pages = list(kwargs["pages"])
        first = pages[0]
        descriptor = first.descriptor
        if mutation == "fragment_count":
            assert descriptor.fragment_count is not None
            descriptor = replace(
                descriptor,
                fragment_count=descriptor.fragment_count + 1,
            )
        elif mutation == "element_digest":
            descriptor = replace(descriptor, element_sha256=f"sha256:{'f' * 64}")
        elif mutation == "fragment_range":
            assert descriptor.fragment_byte_end is not None
            descriptor = replace(
                descriptor,
                fragment_byte_end=descriptor.fragment_byte_end - 1,
            )
        elif mutation == "reconstruction":
            decoded = json.loads(first.content)
            first = replace(
                first,
                content=json.dumps(
                    "x" + decoded[1:],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        else:
            descriptor = replace(descriptor, page_content_sha256=f"sha256:{'f' * 64}")
        pages[0] = replace(first, descriptor=descriptor)
        original_verify(**{**kwargs, "pages": pages})

    monkeypatch.setattr(
        pagination,
        "verify_finalized_recipe_section_plan",
        _verify_corrupted,
    )

    with pytest.raises(RecipeSectionPaginationError, match=message):
        _build(_payload(warnings=["\\" * 5_000]), "warnings", bound=_PAGE_TEST_BOUND)


def test_page_and_fragment_indices_cross_two_digit_boundaries() -> None:
    raw_plan = _build(_payload(content="r" * 120_000), "content", bound=_PAGE_TEST_BOUND)
    assert raw_plan.total_parts > 100
    raw_pages = _decoded_pages(raw_plan, bound=_PAGE_TEST_BOUND)
    assert [raw_pages[index]["part"] for index in (8, 9, 10, 98, 99, 100)] == [
        8,
        9,
        10,
        98,
        99,
        100,
    ]

    fragment_plan = _build(
        _payload(warnings=["\\" * 120_000]),
        "warnings",
        bound=_PAGE_TEST_BOUND,
    )
    fragment_pages = _decoded_pages(fragment_plan, bound=_PAGE_TEST_BOUND)
    assert len(fragment_pages) > 100
    assert [fragment_pages[index]["fragment_index"] for index in (8, 9, 10, 98, 99, 100)] == [
        8,
        9,
        10,
        98,
        99,
        100,
    ]
    assert _reconstruct(fragment_pages) == ["\\" * 120_000]


def test_plan_manifest_is_complete_and_plan_digest_is_non_self_referential() -> None:
    bound = _PAGE_TEST_BOUND
    generation = _generation()
    plan = _build(
        _payload(warnings=["a", "b", "c"] * 20),
        "warnings",
        bound=bound,
        generation=generation,
    )
    pages = _decoded_pages(plan, bound=bound)
    manifest = dataclasses.asdict(plan.manifest)

    assert set(manifest) == {
        "initialization_id",
        "pagination_version",
        "section_registry_sha256",
        "pagination_policy_sha256",
        "generation",
        "section",
        "section_strategy",
        "section_sha256",
        "recipe_section_bound_bytes",
        "pages",
    }
    assert manifest["recipe_section_bound_bytes"] == bound
    assert plan.page_plan_sha256 == recipe_section_plan_digest(plan.manifest)
    assert {page["page_plan_sha256"] for page in pages} == {plan.page_plan_sha256}
    assert len(plan.page_plan_sha256) == len(f"sha256:{'0' * 64}")


def test_string_scalar_strategy_rejects_non_string_values() -> None:
    selected = select_recipe_section(
        _payload(ingredients_table={"not": "markdown"}),
        "ingredients_table",
    )

    with pytest.raises((TypeError, ValueError), match="string"):
        build_recipe_section_page_plan(
            kitchen_id="kitchen-test",
            generation=_generation(),
            selected=selected,
            recipe_section_bound_bytes=_PAGE_TEST_BOUND,
        )


def test_repeat_builds_and_fresh_cache_are_deterministic() -> None:
    payload = _payload(warnings=[f"value-{index}" for index in range(50)])
    first = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)
    second = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)

    assert first == second
    assert _rendered_pages(first) == _rendered_pages(second)
    assert first.page_plan_sha256 == second.page_plan_sha256

    _clear_page_plan_cache()
    third = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)
    assert third == first


def test_cached_plans_are_reused_and_cache_clear_forces_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = select_recipe_section(_payload(content="cache me " * 500), "content")
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-test",
        "generation": _generation(),
        "selected": selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    calls = 0
    real_builder = pagination.build_recipe_section_page_plan

    def counted_builder(**builder_kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        return real_builder(**builder_kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pagination, "build_recipe_section_page_plan", counted_builder)

    first = get_or_build_recipe_section_page_plan(**kwargs)
    second = get_or_build_recipe_section_page_plan(**kwargs)
    assert second is first
    assert calls == 1

    _clear_page_plan_cache()
    third = get_or_build_recipe_section_page_plan(**kwargs)
    assert third == first
    assert third is not first
    assert calls == 2


def test_concurrent_same_key_requests_share_one_page_plan_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = select_recipe_section(_payload(content="single flight"), "content")
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-single-flight",
        "generation": _generation(),
        "selected": selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    start = Barrier(3)
    build_started = Event()
    release_build = Event()
    build_calls: list[None] = []
    real_builder = pagination.build_recipe_section_page_plan

    def blocked_builder(**builder_kwargs: object) -> Any:
        build_calls.append(None)
        build_started.set()
        assert release_build.wait(timeout=5)
        return real_builder(**builder_kwargs)  # type: ignore[arg-type]

    def request_plan() -> Any:
        start.wait(timeout=5)
        return get_or_build_recipe_section_page_plan(**kwargs)

    monkeypatch.setattr(pagination, "build_recipe_section_page_plan", blocked_builder)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request_plan) for _ in range(2)]
        start.wait(timeout=5)
        assert build_started.wait(timeout=5)
        release_build.set()
        plans = [future.result(timeout=5) for future in futures]

    assert len(build_calls) == 1
    assert plans[0] is plans[1]


def test_retirement_during_build_prevents_stale_cache_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = select_recipe_section(_payload(content="retirement race"), "content")
    generation = _generation()
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-retirement-race",
        "generation": generation,
        "selected": selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    key = pagination._cache_key(**kwargs)
    cache = pagination._page_plan_cache()
    build_started = Barrier(2)
    release_build = Event()
    retirement_started = Event()
    real_builder = pagination.build_recipe_section_page_plan

    def blocked_builder(**builder_kwargs: object) -> Any:
        build_started.wait(timeout=5)
        assert release_build.wait(timeout=5)
        return real_builder(**builder_kwargs)  # type: ignore[arg-type]

    def retire_kitchen() -> None:
        retirement_started.set()
        cache.evict_kitchen("kitchen-retirement-race")

    monkeypatch.setattr(pagination, "build_recipe_section_page_plan", blocked_builder)
    with ThreadPoolExecutor(max_workers=2) as executor:
        build_future = executor.submit(get_or_build_recipe_section_page_plan, **kwargs)
        build_started.wait(timeout=5)
        retirement_future = executor.submit(retire_kitchen)
        assert retirement_started.wait(timeout=5)
        release_build.set()
        plan = build_future.result(timeout=5)
        retirement_future.result(timeout=5)

    assert cache.get(key) is None
    cache.put(key, plan)
    assert cache.get(key) is None


@pytest.mark.parametrize(
    "dimension",
    [
        "kitchen",
        "generation",
        "section",
        "section_digest",
        "bound",
        "registry_digest",
        "policy_digest",
        "version",
    ],
)
def test_every_cache_key_dimension_prevents_aliasing(
    dimension: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_selected = select_recipe_section(_payload(content="same"), "content")
    kwargs: dict[str, Any] = {
        "kitchen_id": "kitchen-a",
        "generation": _generation(),
        "selected": baseline_selected,
        "recipe_section_bound_bytes": _PAGE_TEST_BOUND,
    }
    baseline = get_or_build_recipe_section_page_plan(**kwargs)

    if dimension == "kitchen":
        kwargs["kitchen_id"] = "kitchen-b"
    elif dimension == "generation":
        kwargs["generation"] = replace(_generation(), body_size_bytes=2049)
    elif dimension == "section":
        kwargs["selected"] = select_recipe_section(
            _payload(orchestration_rules="same"),
            "orchestration_rules",
        )
    elif dimension == "section_digest":
        kwargs["selected"] = select_recipe_section(_payload(content="changed"), "content")
    elif dimension == "bound":
        kwargs["recipe_section_bound_bytes"] = _PAGE_TEST_BOUND + 1
    elif dimension == "registry_digest":
        # Both the manifest builder (pagination) and the page-body renderer
        # (planning) hold independent imported bindings of this constant —
        # both must move together or manifest/render digests diverge.
        monkeypatch.setattr(
            pagination,
            "RECIPE_SECTION_REGISTRY_DIGEST",
            f"sha256:{'a' * 64}",
        )
        monkeypatch.setattr(
            planning,
            "RECIPE_SECTION_REGISTRY_DIGEST",
            f"sha256:{'a' * 64}",
        )
    elif dimension == "policy_digest":
        monkeypatch.setattr(
            pagination,
            "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
            f"sha256:{'b' * 64}",
        )
        monkeypatch.setattr(
            planning,
            "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
            f"sha256:{'b' * 64}",
        )
    else:
        monkeypatch.setattr(
            pagination,
            "RECIPE_SECTION_PAGINATION_VERSION",
            pagination.RECIPE_SECTION_PAGINATION_VERSION + 1,
        )
        monkeypatch.setattr(
            planning,
            "RECIPE_SECTION_PAGINATION_VERSION",
            planning.RECIPE_SECTION_PAGINATION_VERSION + 1,
        )

    changed = get_or_build_recipe_section_page_plan(**kwargs)
    assert changed is not baseline
    assert get_or_build_recipe_section_page_plan(**kwargs) is changed


def test_cache_entry_limit_evicts_oldest_plan() -> None:
    selected = select_recipe_section(_payload(content="entry eviction"), "content")
    generation = _generation()
    first = get_or_build_recipe_section_page_plan(
        kitchen_id="kitchen-0",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    for index in range(1, pagination.PAGE_PLAN_CACHE_MAX_ENTRIES + 1):
        get_or_build_recipe_section_page_plan(
            kitchen_id=f"kitchen-{index}",
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=_PAGE_TEST_BOUND,
        )

    rebuilt = get_or_build_recipe_section_page_plan(
        kitchen_id="kitchen-0",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    assert rebuilt == first
    assert rebuilt is not first


def test_cache_rejects_a_single_plan_over_its_byte_limit() -> None:
    selected = select_recipe_section(_payload(content="overweight"), "content")
    generation = _generation()
    plan = _build(_payload(content="overweight"), "content", bound=_PAGE_TEST_BOUND)
    key = pagination._cache_key(
        kitchen_id="kitchen-overweight",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    cache = PagePlanCache(max_bytes=plan.cache_weight_bytes - 1)

    cache.put(key, plan)

    assert cache.get(key) is None
    assert cache._weight_bytes == 0


def test_cache_byte_limit_evicts_oldest_plan() -> None:
    selected = select_recipe_section(_payload(content="byte eviction"), "content")
    generation = _generation()
    plan = dataclasses.replace(
        _build(_payload(content="byte eviction"), "content", bound=_PAGE_TEST_BOUND),
        cache_weight_bytes=10,
    )
    keys = [
        pagination._cache_key(
            kitchen_id=f"kitchen-{index}",
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=_PAGE_TEST_BOUND,
        )
        for index in range(3)
    ]
    cache = PagePlanCache(max_entries=10, max_bytes=20)

    for key in keys:
        cache.put(key, plan)

    assert cache.get(keys[0]) is None
    assert cache.get(keys[1]) is plan
    assert cache.get(keys[2]) is plan
    assert cache._weight_bytes == 20


def test_cache_replacement_subtracts_the_previous_plan_weight() -> None:
    selected = select_recipe_section(_payload(content="replacement"), "content")
    generation = _generation()
    base_plan = _build(_payload(content="replacement"), "content", bound=_PAGE_TEST_BOUND)
    first = dataclasses.replace(base_plan, cache_weight_bytes=7)
    replacement = dataclasses.replace(base_plan, cache_weight_bytes=3)
    other = dataclasses.replace(base_plan, cache_weight_bytes=7)
    key = pagination._cache_key(
        kitchen_id="kitchen-replacement",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    other_key = pagination._cache_key(
        kitchen_id="kitchen-other",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    cache = PagePlanCache(max_entries=10, max_bytes=10)

    cache.put(key, first)
    cache.put(key, replacement)
    cache.put(other_key, other)

    assert cache.get(key) is replacement
    assert cache.get(other_key) is other
    assert cache._weight_bytes == 10


def test_cache_kitchen_eviction_subtracts_every_matching_plan_weight() -> None:
    selected = select_recipe_section(_payload(content="kitchen eviction"), "content")
    generation = _generation()
    plan = dataclasses.replace(
        _build(_payload(content="kitchen eviction"), "content", bound=_PAGE_TEST_BOUND),
        cache_weight_bytes=5,
    )
    retired_keys = [
        pagination._cache_key(
            kitchen_id="retired-kitchen",
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=bound,
        )
        for bound in (_PAGE_TEST_BOUND, _PAGE_TEST_BOUND + 1)
    ]
    retained_key = pagination._cache_key(
        kitchen_id="retained-kitchen",
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=_PAGE_TEST_BOUND,
    )
    cache = PagePlanCache(max_entries=10, max_bytes=20)
    for key in (*retired_keys, retained_key):
        cache.put(key, plan)

    cache.evict_kitchen("retired-kitchen")

    assert all(cache.get(key) is None for key in retired_keys)
    assert cache.get(retained_key) is plan
    assert cache._weight_bytes == plan.cache_weight_bytes


def test_cross_process_plan_and_rendering_are_deterministic() -> None:
    payload = _payload(warnings=["alpha", "snowman-☃", 'quote-"', "slash-\\"] * 20)
    local = _build(payload, "warnings", bound=_PAGE_TEST_BOUND)
    local_render_sha = hashlib.sha256(
        "\0".join(_rendered_pages(local)).encode("utf-8")
    ).hexdigest()
    script = f"""
import hashlib
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    RecipeArtifactGeneration,
)
from autoskillit.server._recipe_section_pagination import (
    build_recipe_section_page_plan,
    render_recipe_section_page,
    select_recipe_section,
)
payload = {payload!r}
generation = RecipeArtifactGeneration(
    producer_tool="open_kitchen",
    recipe_name="remediation",
    descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
    payload_sha256="sha256:" + "1" * 64,
    artifact_blob_sha256="sha256:" + "2" * 64,
    artifact_blob_size_bytes=4096,
    body_sha256="sha256:" + "3" * 64,
    body_size_bytes=2048,
    flow_schema_version=RECIPE_FLOW_SCHEMA_VERSION,
    flow_sha256="sha256:" + "4" * 64,
    flow_size_bytes=512,
    flow_record_count=2,
)
plan = build_recipe_section_page_plan(
    kitchen_id="kitchen-test",
    generation=generation,
    selected=select_recipe_section(payload, "warnings"),
    recipe_section_bound_bytes=2000,
)
rendered = [render_recipe_section_page(plan, part) for part in range(plan.total_parts)]
print(plan.page_plan_sha256)
print(hashlib.sha256("\\0".join(rendered).encode("utf-8")).hexdigest())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.stdout.splitlines() == [
        local.page_plan_sha256,
        local_render_sha,
    ]
