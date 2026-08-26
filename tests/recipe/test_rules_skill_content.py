"""Anchor file for the legacy `tests/recipe/test_rules_skill_content.py` cascade stem.

The bulk of this file's tests were split into four per-family test files
in `tests/recipe/rules_skills/` as part of the #4852 decomposition. The
15-rule registration contract is now asserted centrally in
`tests/recipe/rules_skills/test_split_rule_registration.py`; per-rule
behavior is covered in the per-family files.

This file is retained as a cascade anchor for `rules_skill_content` in
`tests/_test_filter.py::MODULE_CASCADE_RECIPE` so that the facade's
filter entry keeps a discoverable target. The single trivial smoke test
keeps the cascade guard, layer marker, and size marker contracts happy.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_facade_module_is_importable() -> None:
    """The `rules_skill_content` facade module must remain importable."""
    import autoskillit.recipe.rules.rules_skill_content  # noqa: F401
