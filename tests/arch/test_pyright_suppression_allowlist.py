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
    (
        "recipe/__init__.py",
        373,
    ): "lazy-registry: _reg._finalize_registry() attribute access on dynamically-built registry",
    ("recipe/api_orchestration/_api_orchestration_cache.py", 144): (
        "lazy-registry: RULE_REGISTRY_HASH set by _finalize_registry()"
    ),
}

TEST_ALLOWLIST: dict[tuple[str, int], str] = {
    (
        "arch/test_recipe_rule_registration.py",
        74,
    ): "global-mutated variable Pyright cannot resolve",
    ("recipe/test_research_campaign_rules.py", 7): "side-effect import for rule registration",
    ("recipe/test_research_sub_recipe_rules.py", 9): "side-effect import for rule registration",
}

# The exploration identity guard has two standalone sibling imports that static
# analysis cannot resolve through its runtime hooks-directory path bootstrap.
# The join batch machinery (#4575) adds 14 site-bounded # type: ignore comments
# across the declare_join_batch handler, the join ledger, and the Join-guard
# hook scripts; the runtime join ledger is stdlib-only and the bridge layers
# cannot be statically resolved from outside the hooks/ subtree.
# Bumped from 140 to 144 after rebase onto develop (#4853 added 4 net
# # type: ignore[import-not-found] suppressions on standalone guard scripts).
# Bumped from 144 to 155 after rebase onto develop (#4851 adds 9 site-bounded
# # type: ignore comments in fleet/dispatch/_*.py for cross-phase SpawnContext /
# DispatchResult field threading that pyright cannot narrow through the
# module-attribute indirection used for monkeypatch-friendly imports).
# Bumped from 155 to 156. Issue #4349's rectify adds a
# `from quota_constraints import (...)  # type: ignore[import-not-found]` block to
# both quota_guard.py and quota_post_hook.py (+2), following the existing
# `from quota import (...)` suppression already present in both files for the
# same stdlib-only bare-module hook bootstrap that cannot be statically resolved.
# Merging in develop's own progress since this branch's fork point separately
# brings in #4926's `hooks/_capture_spawn.py:215` (+1, `# type: ignore[has-type]`
# on a module-level logger reassignment). Net of both: 153 (fork point) + 2 + 1 = 156.
# The managed fixed-batch route and its stdlib-only join shards add three net
# standalone-import suppressions; hook subprocesses resolve sibling modules
# through their runtime path bootstrap.
TYPE_IGNORE_BUDGET = 159


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
    assert count <= TYPE_IGNORE_BUDGET, (
        f"type: ignore count ({count}) exceeds budget ({TYPE_IGNORE_BUDGET}). "
        "Review new suppressions — they may indicate real type errors."
    )
