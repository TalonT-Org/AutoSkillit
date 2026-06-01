"""
Pyright suppression allowlist — REQ-PYRIGHT-001.

Every pyright suppression comment in production code must appear in an
explicit allowlist. Unlisted suppressions fail CI.
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit"
TESTS = Path(__file__).resolve().parent.parent

_PYRIGHT_RE = re.compile(r"#\s*pyright:\s*ignore|#.*--\s*pyright:\s*ignore")

PRODUCTION_ALLOWLIST: dict[tuple[str, int], str] = {
    ("recipe/__init__.py", 228): "dynamic method on lazy-registry object",
    ("recipe/_api.py", 258): "global-mutated variable set by _finalize_registry()",
}

TEST_ALLOWLIST: dict[tuple[str, int], str] = {
    (
        "arch/test_recipe_rule_registration.py",
        74,
    ): "global-mutated variable Pyright cannot resolve",
    ("recipe/test_research_campaign_rules.py", 7): "side-effect import for rule registration",
    ("recipe/test_research_sub_recipe_rules.py", 9): "side-effect import for rule registration",
}


def _scan_pyright_ignores(root: Path) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PYRIGHT_RE.search(line):
                found.add((str(path.relative_to(root)), i))
    return found


def test_production_pyright_suppressions_are_allowlisted() -> None:
    found = _scan_pyright_ignores(SRC)
    allowed = set(PRODUCTION_ALLOWLIST.keys())
    unlisted = found - allowed
    assert not unlisted, (
        "Unregistered pyright suppression(s) in production code:\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in sorted(unlisted))
        + "\nIf legitimate, add to PRODUCTION_ALLOWLIST with justification."
    )
    stale = allowed - found
    assert not stale, "Stale allowlist entry — suppression no longer exists:\n" + "\n".join(
        f"  {p}:{ln}" for p, ln in sorted(stale)
    )


def test_test_pyright_suppressions_are_allowlisted() -> None:
    found = _scan_pyright_ignores(TESTS)
    allowed = set(TEST_ALLOWLIST.keys())
    unlisted = found - allowed
    assert not unlisted, (
        "Unregistered pyright suppression(s) in test code:\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in sorted(unlisted))
        + "\nIf legitimate, add to TEST_ALLOWLIST with justification."
    )
    stale = allowed - found
    assert not stale, "Stale allowlist entry — suppression no longer exists:\n" + "\n".join(
        f"  {p}:{ln}" for p, ln in sorted(stale)
    )


def test_type_ignore_count_budget() -> None:
    """Guard against unbounded growth of type: ignore suppressions."""
    count = 0
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "# type: ignore" in line:
                count += 1
    budget = 94
    assert count <= budget, (
        f"type: ignore count ({count}) exceeds budget ({budget}). "
        "Review new suppressions — they may indicate real type errors."
    )
