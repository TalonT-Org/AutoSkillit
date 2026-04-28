"""Guard against re-introduction of _get_skill_category_map duplication.

Asserts that both rule modules share the exact same function object, meaning
the function is defined once in a shared helper and imported in both places.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_skill_category_map_not_duplicated() -> None:
    """rules_skills and rules_features must share _get_skill_category_map identity.

    If this test fails, someone re-introduced a local definition in one of the
    two files. The function must live solely in _skill_helpers and be imported.
    """
    from autoskillit.recipe import rules_features, rules_skills

    assert rules_skills._get_skill_category_map is rules_features._get_skill_category_map, (
        "_get_skill_category_map must be the same object in rules_skills and "
        "rules_features — define it once in recipe/_skill_helpers.py and import it "
        "in both modules."
    )
