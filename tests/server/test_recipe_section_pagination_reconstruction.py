"""Raw, scalar, and array pagination reconstruction contracts."""

from __future__ import annotations

import pytest

from autoskillit.core import recipe_section_digest, recipe_section_element_digest
from autoskillit.server import _recipe_section_pagination as pagination
from autoskillit.server._recipe_artifact import extract_recipe_step_bodies
from autoskillit.server._recipe_section_pagination import (
    PagePlanCache,
    render_recipe_section_page,
)
from tests.server._recipe_section_pagination_test_helpers import (
    _PAGE_TEST_BOUND,
    _build,
    _decoded_pages,
    _payload,
    _reconstruct,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.fixture(autouse=True)
def _fresh_page_plan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pagination, "_PAGE_PLAN_CACHE", PagePlanCache())


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
    assert bodies[0][1] == '{"second":{"action":"stop","message":"done"}}'
    assert bodies[1][1] == '{"first":{"tool":"run_cmd","with":{"cmd":"echo first"}}}'


@pytest.mark.parametrize(
    "value",
    [
        "plain ASCII text " * 100,
        "snowman ☃ and emoji 🥘 " * 100,
        'quotes " and backslashes \\\\ ' * 100,
        "tabs\tnewlines\ncarriage\rcontrols " * 100,
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
