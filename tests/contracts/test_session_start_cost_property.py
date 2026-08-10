"""Property checks for bounded recipe session-start growth."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from autoskillit.core import BoundedDeliveryRoundTripBudgetExceededError
from autoskillit.server._recipe_delivery import validate_compiled_recipe_delivery_budget
from tests.contracts._delivery_constants import MAX_BYTES_PER_PAGE, MAX_PAGES_PER_SECTION

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@given(st.integers(min_value=10_000, max_value=500_000))
@settings(max_examples=50, deadline=None)
def test_session_start_cost_rejects_content_over_page_budget(body_bytes: int) -> None:
    content_pages = math.ceil(body_bytes / MAX_BYTES_PER_PAGE)
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
