"""Property checks for bounded recipe session-start growth."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from autoskillit.core import BoundedDeliveryRoundTripBudgetExceededError
from autoskillit.server._recipe_delivery import validate_compiled_recipe_delivery_budget
from tests.contracts._delivery_constants import MAX_PAGES_PER_SECTION, MAX_TOKENS_PER_PAGE

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@given(st.integers(min_value=10_000, max_value=500_000))
@settings(max_examples=50, deadline=None)
def test_session_start_cost_grows_sublinearly_in_body_size(body_bytes: int) -> None:
    content_pages = math.ceil(body_bytes / MAX_TOKENS_PER_PAGE)
    counts = (content_pages, 1)
    if content_pages > MAX_PAGES_PER_SECTION:
        with pytest.raises(BoundedDeliveryRoundTripBudgetExceededError):
            validate_compiled_recipe_delivery_budget(
                recipe="synthetic",
                backend="property",
                section_page_counts=counts,
            )
    else:
        validate_compiled_recipe_delivery_budget(
            recipe="synthetic",
            backend="property",
            section_page_counts=counts,
        )


@given(st.lists(st.integers(min_value=10_000, max_value=500_000), min_size=1, max_size=10))
@settings(max_examples=30, deadline=None)
def test_multi_section_page_growth_is_linear_not_quadratic(section_sizes: list[int]) -> None:
    pages = [math.ceil(size / MAX_TOKENS_PER_PAGE) for size in section_sizes]
    admitted_pages = sum(min(count, MAX_PAGES_PER_SECTION) for count in pages)
    assert admitted_pages <= len(section_sizes) * MAX_PAGES_PER_SECTION
