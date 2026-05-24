"""Contract: every fleet-injected env var must appear in at least one filter list."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_FLEET_INJECTED_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_DISPATCH_ID",
        "AUTOSKILLIT_SESSION_DEADLINE",
    }
)


def test_fleet_injected_vars_covered_by_filter_lists() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    combined = AUTOSKILLIT_PRIVATE_ENV_VARS | _HEADLESS_EXCLUSIVE_VARS
    uncovered = _FLEET_INJECTED_VARS - combined
    assert not uncovered, (
        f"Fleet-injected vars missing from both filter lists: {uncovered}. "
        f"Add each to AUTOSKILLIT_PRIVATE_ENV_VARS or _HEADLESS_EXCLUSIVE_VARS."
    )
