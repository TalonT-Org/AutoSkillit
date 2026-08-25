"""Pagination planning and error-case contracts for recipe section pagination."""

from __future__ import annotations

import dataclasses
import json

import pytest

from autoskillit.core import (
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_SECTION_MANDATORY_FAILURE_CODES,
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
    client_serialized_char_len,
)
from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server import _recipe_section_planning as planning
from autoskillit.server._recipe_initialization import recipe_initialization_receipt
from autoskillit.server._recipe_section_pagination import (
    PagePlanCache,
    RecipeSectionBoundError,
    RecipeSectionPageDescriptor,
    RecipeSectionRequestState,
    build_recipe_section_page_plan,
    render_recipe_section_failure,
    select_recipe_section,
)
from tests.server._recipe_section_pagination_helpers import (
    _PAGE_TEST_BOUND,
    _build,
    _generation,
    _payload,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.fixture(autouse=True)
def _fresh_page_plan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", PagePlanCache())


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


def test_convergence_ceiling_is_derived_from_artifact_policy() -> None:
    max_count_digits = len(str(RECIPE_ARTIFACT_MAX_BLOB_BYTES))

    assert pagination._convergence_iteration_ceiling() == 1 + (
        (RECIPE_ARTIFACT_MAX_BLOB_BYTES + 1) * (max_count_digits - 1)
    )
    assert pagination._convergence_iteration_ceiling() > 32
