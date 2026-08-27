"""Split-invariant: every rules_merge* module is at or under 750 lines.

Issue #4857 acceptance criterion (a): ``rules_merge.py`` is reduced to a thin
facade and every helper module is at or under the 750-line ceiling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RULES_DIR = _REPO_ROOT / "src" / "autoskillit" / "recipe" / "rules"
_CEILING = 750

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_rules_merge_facade_is_under_ceiling() -> None:
    """The facade must be at or under the ceiling AND contain no @semantic_rule decorators.

    The decoration contract requires every ``@semantic_rule`` to live in a
    sibling module, not the facade — otherwise registration still works but
    violates the decomposition's intent (rules_merge.py stays registration-only).
    """
    import re as _re

    facade = _RULES_DIR / "rules_merge.py"
    text = facade.read_text()
    lines = text.splitlines()
    assert len(lines) <= _CEILING, f"Facade {facade.name} is {len(lines)} lines (limit {_CEILING})"
    # Match decorator usage at column 0 (not references in docstrings/strings).
    decorator_re = _re.compile(r"^@semantic_rule\b", _re.MULTILINE)
    assert not decorator_re.search(text), (
        "Facade rules_merge.py must not declare any @semantic_rule decorators; "
        "all rule registration must be in the sibling modules."
    )


@pytest.mark.parametrize(
    "filename",
    [
        "rules_merge_routing.py",
        "rules_merge_guards.py",
        "rules_merge_wait.py",
        "rules_merge_enrollment.py",
        "rules_merge_push_symmetry.py",
    ],
)
def test_each_extracted_module_is_under_ceiling(filename: str) -> None:
    """Each new sibling must be at or under the 750-line ceiling."""
    module = _RULES_DIR / filename
    lines = module.read_text().splitlines()
    assert len(lines) <= _CEILING, f"Sibling {filename} is {len(lines)} lines (limit {_CEILING})"
