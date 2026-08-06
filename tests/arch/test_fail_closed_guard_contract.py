"""Standing invariants for the fail-closed guard registry.

Every guard listed in FAIL_CLOSED_GUARD_BASENAMES must be a live, registered
guard script, and must carry its own false-positive (allow) test corpus.
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

# "approve" is included because tests/infra/test_skill_command_guard.py pairs
# each deny test with an approve-named allow test.
_ALLOW_TEST_NAME_RE = re.compile(r"test_.*(allow|permit|not_blocked|approve)")
_GUARD_RESULT_NAMES = frozenset(
    {"buf", "decision", "hook_out", "out", "output", "response", "result"}
)
_GUARD_RESULT_ATTRIBUTES = frozenset({"permissionDecision", "stdout"})


def _references_guard_result(node: ast.AST) -> bool:
    return any(
        (isinstance(descendant, ast.Name) and descendant.id.lstrip("_") in _GUARD_RESULT_NAMES)
        or (isinstance(descendant, ast.Attribute) and descendant.attr in _GUARD_RESULT_ATTRIBUTES)
        for descendant in ast.walk(node)
    )


def _is_allow_value(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and node.value in {None, ""}) or (
        isinstance(node, ast.Dict) and not node.keys
    )


def _has_behavioral_allow_assertion(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Assert) or not _references_guard_result(node.test):
            continue
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return True
        if not isinstance(test, ast.Compare):
            continue
        for operator, comparator in zip(test.ops, test.comparators, strict=True):
            if isinstance(operator, (ast.Eq, ast.Is)) and _is_allow_value(comparator):
                return True
            if (
                isinstance(operator, ast.NotEq)
                and isinstance(comparator, ast.Constant)
                and comparator.value == "deny"
            ):
                return True
    return False


def _module_has_behavioral_allow_test(source: str) -> bool:
    return any(
        _ALLOW_TEST_NAME_RE.search(node.name) and _has_behavioral_allow_assertion(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_allow_test_detection_requires_behavioral_assertion() -> None:
    name_only = "def test_allows_valid_input():\n    pass\n"
    behavioral = (
        "def test_allows_valid_input():\n    out = run_guard()\n    assert not out.strip()\n"
    )

    assert not _module_has_behavioral_allow_test(name_only)
    assert _module_has_behavioral_allow_test(behavioral)


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

    A behavioral allow-test neighbor keeps false-positive protection paired
    with every fail-closed guard as a standing, self-enforcing invariant.
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
            if _module_has_behavioral_allow_test(candidate.read_text(encoding="utf-8")):
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
