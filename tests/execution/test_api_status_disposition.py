"""Default-deny classification coverage for provider API statuses."""

from __future__ import annotations

import pytest

from autoskillit.core import InfraExitCategory
from autoskillit.execution.session._exit_classification import classify_api_status

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_RETRIABLE_STATUSES = frozenset({408, 409})


@pytest.mark.parametrize("status", range(400, 600))
def test_api_status_disposition_is_total_and_default_deny(status: int) -> None:
    category = classify_api_status(status)

    assert category in {
        InfraExitCategory.RATE_LIMITED,
        InfraExitCategory.API_ERROR,
        InfraExitCategory.API_ERROR_TERMINAL,
    }
    if status == 429:
        assert category is InfraExitCategory.RATE_LIMITED
    elif status in _RETRIABLE_STATUSES or status >= 500:
        assert category is InfraExitCategory.API_ERROR
    else:
        assert category is InfraExitCategory.API_ERROR_TERMINAL
