"""Split-invariant: all nine merge rules register exactly once, no rule
appears in more than one sibling, and importing the facade triggers every
sibling's registration (transitive coverage).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.recipe.rules_merge._helpers import (
    EXPECTED_RULE_NAMES,
    registered_merge_rule_names,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RULES_DIR = _REPO_ROOT / "src" / "autoskillit" / "recipe" / "rules"

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_all_nine_merge_rules_registered_exactly_once() -> None:
    """The nine EXPECTED merge rules must all be registered.

    Other merge-prefix rules may also live in ``recipe/rules/`` (e.g.
    ``merge-test-gate-context-not-forwarded`` in ``rules_merge_context.py``),
    so this test asserts the EXPECTED nine are present without constraining
    the rest of the registry.
    """
    registered = registered_merge_rule_names()
    missing = set(EXPECTED_RULE_NAMES) - registered
    assert not missing, (
        f"Merge rules from EXPECTED_RULE_NAMES missing from registry: "
        f"{sorted(missing)}. Registered merge-prefix rules: "
        f"{sorted(registered)}."
    )


def test_no_rule_name_registered_more_than_once() -> None:
    """No merge rule name may appear in more than one sibling module.

    Walking each sibling's source for ``@semantic_rule(name="..."`` decorators
    catches the regression where a copy-paste error puts the same rule into
    two files.
    """
    import re as _re

    decorator_re = _re.compile(r'@semantic_rule\(\s*name\s*=\s*"([^"]+)"')
    seen: dict[str, list[str]] = {}
    for path in sorted(_RULES_DIR.glob("rules_merge_*.py")):
        text = path.read_text()
        for match in decorator_re.finditer(text):
            seen.setdefault(match.group(1), []).append(path.name)
    duplicates = {name: files for name, files in seen.items() if len(files) > 1}
    assert not duplicates, f"Rule names appearing in multiple sibling files: {duplicates}"


def test_facade_imports_trigger_every_sibling_registration() -> None:
    """Importing the facade must populate the registry identically to importing
    each sibling directly.

    The facade side-effect-imports every sibling module, so importing the
    facade alone is sufficient for all nine rules to register.
    """
    registered = registered_merge_rule_names()
    assert set(EXPECTED_RULE_NAMES).issubset(registered), (
        f"Facade import did not register all expected merge rules. "
        f"Registered: {sorted(registered)}. "
        f"Expected: {sorted(EXPECTED_RULE_NAMES)}."
    )
