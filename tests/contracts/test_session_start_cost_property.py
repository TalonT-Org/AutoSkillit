"""Property checks for bounded recipe session-start growth."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from autoskillit.core import BoundedDeliveryRoundTripBudgetExceededError
from autoskillit.server._recipe_delivery import validate_compiled_recipe_delivery_budget
from tests.contracts._delivery_constants import MAX_BYTES_PER_PAGE

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@given(st.integers(min_value=1, max_value=500_000))
@settings(max_examples=50, deadline=None)
def test_session_start_cost_accepts_any_positive_page_count(body_bytes: int) -> None:
    """A bounded delivery plan terminates for any positive page count, however
    large -- multi-page plans are slower but valid, not hard errors."""
    content_pages = math.ceil(body_bytes / MAX_BYTES_PER_PAGE)
    counts = (content_pages, 1)
    validate_compiled_recipe_delivery_budget(
        recipe="synthetic",
        backend="property",
        section_page_counts=counts,
    )


def test_session_start_cost_rejects_non_terminating_zero_page_section() -> None:
    """A section that cannot fit even a single element (zero compiled pages)
    is non-terminating and must be rejected."""
    with pytest.raises(BoundedDeliveryRoundTripBudgetExceededError):
        validate_compiled_recipe_delivery_budget(
            recipe="synthetic",
            backend="property",
            section_page_counts=(0, 1),
        )
