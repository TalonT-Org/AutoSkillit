"""Integration tests for migration/_api.py — no mocking of recipe lookup or engine."""
from __future__ import annotations

import pytest

from autoskillit.migration._api import check_and_migrate

pytestmark = [pytest.mark.layer("migration"), pytest.mark.medium]


@pytest.mark.anyio
async def test_check_and_migrate_integration_known_recipe_up_to_date() -> None:
    """Integration: no mocks — check_and_migrate with real bundled recipe returns up_to_date.

    Uses the real find_recipe_by_name and applicable_migrations. At the current installed
    version, no migrations are pending for a bundled recipe, so the engine is never entered.
    """
    import autoskillit
    from autoskillit.core import pkg_root

    result = await check_and_migrate("implementation", pkg_root(), autoskillit.__version__)

    assert result.get("status") == "up_to_date"
    assert result.get("name") == "implementation"
    assert "error" not in result
