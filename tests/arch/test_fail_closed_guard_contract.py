"""Standing invariants for the fail-closed guard registry.

Every guard listed in FAIL_CLOSED_GUARD_BASENAMES must be a live, registered
guard script, and must carry its own false-positive (allow) test corpus —
the gap that let github_mutation_guard.py ship without one (see the
Rectify plan's "How Tests Missed This" section).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from autoskillit.hook_registry import FAIL_CLOSED_GUARD_BASENAMES, HOOK_REGISTRY

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARDS_DIR = _REPO_ROOT / "src" / "autoskillit" / "hooks" / "guards"
_TESTS_ROOT = _REPO_ROOT / "tests"

# "approve" is included alongside the plan's allow/permit/not_blocked vocabulary:
# tests/infra/test_skill_command_guard.py already pairs each deny test with an
# approve-named allow test, predating this contract.
# This is deliberately a structural neighbor check; assertion quality remains
# the responsibility of each guard's behavioral test suite.
_ALLOW_TEST_NAME_RE = re.compile(r"test_.*(allow|permit|not_blocked|approve)")


def _module_test_function_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            names.add(node.name)
    return names


def test_fail_closed_guard_basenames_are_live_files() -> None:
    """Every registered fail-closed guard basename exists under hooks/guards/."""
    live = {path.name for path in _GUARDS_DIR.glob("*.py")}
    missing = FAIL_CLOSED_GUARD_BASENAMES - live
    assert not missing, (
        f"FAIL_CLOSED_GUARD_BASENAMES references files not present under {_GUARDS_DIR}: "
        f"{sorted(missing)}"
    )


def test_fail_closed_guard_basenames_are_registered() -> None:
    """Every registered fail-closed guard basename is referenced by some HOOK_REGISTRY entry."""
    registered_scripts = {
        Path(script).name for hook_def in HOOK_REGISTRY for script in hook_def.scripts
    }
    unregistered = FAIL_CLOSED_GUARD_BASENAMES - registered_scripts
    assert not unregistered, (
        "FAIL_CLOSED_GUARD_BASENAMES contains basenames absent from HOOK_REGISTRY: "
        f"{sorted(unregistered)}"
    )


def test_every_fail_closed_guard_has_an_allow_test() -> None:
    """Every fail-closed guard's test file exists and pins at least one allow case.

    A guard authored without a matched allow-test neighbor is exactly how
    github_mutation_guard.py's cwd-equality over-block shipped undetected —
    this makes that gap a standing, self-enforcing invariant instead of a
    one-time fix.
    """
    missing_files: list[str] = []
    missing_allow_tests: list[str] = []

    for basename in sorted(FAIL_CLOSED_GUARD_BASENAMES):
        stem = Path(basename).stem
        candidates = sorted(_TESTS_ROOT.glob(f"**/test_{stem}.py"))
        if not candidates:
            missing_files.append(f"{basename} (expected tests/**/test_{stem}.py)")
            continue

        found_allow_test = False
        for candidate in candidates:
            names = _module_test_function_names(candidate.read_text())
            if any(_ALLOW_TEST_NAME_RE.search(name) for name in names):
                found_allow_test = True
                break
        if not found_allow_test:
            rels = ", ".join(str(c.relative_to(_TESTS_ROOT)) for c in candidates)
            missing_allow_tests.append(
                f"{basename} — no test function matching {_ALLOW_TEST_NAME_RE.pattern!r} "
                f"in: {rels}"
            )

    assert not missing_files, "Missing test files for fail-closed guards:\n" + "\n".join(
        f"  {m}" for m in missing_files
    )
    assert not missing_allow_tests, (
        "Fail-closed guards missing an allow-test neighbor:\n"
        + "\n".join(f"  {m}" for m in missing_allow_tests)
    )
