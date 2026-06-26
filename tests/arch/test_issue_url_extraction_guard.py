"""AST guard: raw ``.get('issue_url')`` / ``.get('issue_urls')`` calls banned in ``fleet/``.

Issue #4112 defense-in-depth: the canonical extraction function lives in
``_issue_url_helpers.py``, and any raw ``.get()`` call elsewhere in ``fleet/``
(except ``_issue_url_helpers.py`` itself and ``state_types.py``, which
deserializes ``DispatchRecord`` from its own JSON dict) would re-introduce the
singular/plural key mismatch that orphaned labels for the 7th time.

Also enforces that ``fleet_claim_guard.py`` retains BOTH key variants in its
inline dual-key lookup — the guard is a stdlib-only hook script and cannot
import the canonical helper, so it keeps its inline pattern and this test
guards that both ``issue_urls`` and ``issue_url`` keys remain present.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "fleet"
HOOK_GUARD_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "hooks"
    / "guards"
    / "fleet_claim_guard.py"
)

# Files exempt from the raw-``get`` ban:
# - ``_issue_url_helpers.py``: defines the canonical accessor.
# - ``state_types.py``: deserializes ``DispatchRecord`` from its own JSON dict.
EXEMPT_FILES: frozenset[str] = frozenset({"_issue_url_helpers.py", "state_types.py"})

BANNED_KEYS: frozenset[str] = frozenset({"issue_url", "issue_urls"})


def _find_issue_url_get_calls(path: Path) -> list[tuple[int, str]]:
    """Return ``(line, key)`` tuples for raw ``.get`` calls on issue-URL keys."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant):
            continue
        if first_arg.value in BANNED_KEYS:
            results.append((node.lineno, first_arg.value))
    return results


def test_no_raw_issue_url_get_in_fleet() -> None:
    """Raw ``.get('issue_url')`` / ``.get('issue_urls')`` are banned in fleet/.

    All fleet code must route through ``extract_issue_urls()`` to guarantee the
    dual-key lookup is applied uniformly. Any direct ``.get()`` call would
    re-introduce the singular/plural mismatch.
    """
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.glob("*.py")):
        if py_file.name in EXEMPT_FILES:
            continue
        for lineno, key in _find_issue_url_get_calls(py_file):
            violations.append(f"{py_file.name}:{lineno} (.get({key!r}))")

    assert not violations, (
        "Raw .get('issue_url') / .get('issue_urls') calls in fleet/ must go "
        f"through extract_issue_urls() in _issue_url_helpers.py. Found in: {violations}"
    )


def test_fleet_claim_guard_has_dual_key_lookup() -> None:
    """``fleet_claim_guard.py`` must look up BOTH ``issue_urls`` AND ``issue_url``.

    The hook is stdlib-only and cannot import ``extract_issue_urls()``, so it
    keeps its inline dual-key pattern. This test enforces that both key
    variants remain present — if a contributor removes either the plural or
    singular lookup, this test fails.
    """
    assert HOOK_GUARD_PATH.exists(), f"Hook guard not found at {HOOK_GUARD_PATH}"
    found_keys = {key for _, key in _find_issue_url_get_calls(HOOK_GUARD_PATH)}

    assert "issue_urls" in found_keys, (
        "fleet_claim_guard.py must look up the plural 'issue_urls' key."
    )
    assert "issue_url" in found_keys, (
        "fleet_claim_guard.py must look up the singular 'issue_url' key "
        "(dual-key ingredient access is required)."
    )
