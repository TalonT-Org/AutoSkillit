"""Keep protected-path guard coverage selected for hooks classifier changes."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_CLASSIFIER_MODULES = frozenset(
    {
        "autoskillit.hooks._classification._flags",
        "autoskillit.hooks._runtime._command_classification",
    }
)
_CLASSIFIER_SYMBOLS = frozenset(
    {
        "PROTECTED_SOURCE_PATH_PATTERNS",
        "command_has_blocked_protected_path_read",
        "is_allowed_protected_path_metadata_command",
    }
)
_CORPUS_MODULE = "tests.infra._protected_path_command_corpus"
_CORPUS_SYMBOLS = frozenset({"ADMITTED_COMMANDS", "DENIED_COMMANDS"})
_EXPECTED_IMPORT_CONSUMERS = {
    "arch/test_intake_rule_registry.py": frozenset(
        {"PROTECTED_SOURCE_PATH_PATTERNS", "command_has_blocked_protected_path_read"}
    ),
    "hooks/test_command_classification.py": frozenset(
        {
            "command_has_blocked_protected_path_read",
            "is_allowed_protected_path_metadata_command",
        }
    ),
    "infra/test_recipe_read_guard.py": _CORPUS_SYMBOLS,
    "server/test_tools_run_cmd_invariants.py": _CORPUS_SYMBOLS,
}
_REQUIRED_GUARD_TESTS = frozenset(_EXPECTED_IMPORT_CONSUMERS)
_HOOK_CLASSIFIER_CHANGE = "src/autoskillit/hooks/_classification/_flags.py"


def _protected_path_import_consumers() -> dict[str, frozenset[str]]:
    consumers: dict[str, frozenset[str]] = {}
    for path in sorted(_TESTS_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classifier_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in _CLASSIFIER_MODULES
            for alias in node.names
            if alias.name in _CLASSIFIER_SYMBOLS
        }
        corpus_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == _CORPUS_MODULE
            for alias in node.names
            if alias.name in _CORPUS_SYMBOLS
        }
        imported = classifier_imports | corpus_imports
        if imported:
            consumers[path.relative_to(_TESTS_ROOT).as_posix()] = frozenset(imported)
    return consumers


def _scope_selects(scope: set[Path], test_path: str) -> bool:
    target = _TESTS_ROOT / test_path
    return any(selected == target or selected in target.parents for selected in scope)


def test_protected_path_corpus_and_classifier_import_inventory_is_bidirectional() -> None:
    assert _protected_path_import_consumers() == _EXPECTED_IMPORT_CONSUMERS


@pytest.mark.parametrize("mode", ["conservative", "aggressive"])
def test_hooks_classifier_change_selects_all_protected_path_guard_tests(mode: str) -> None:
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope

    scope = build_test_scope(
        changed_files={_HOOK_CLASSIFIER_CHANGE},
        mode=FilterMode(mode),
        tests_root=_TESTS_ROOT,
    )

    assert not isinstance(scope, FullRunReason)
    missing = sorted(
        test_path for test_path in _REQUIRED_GUARD_TESTS if not _scope_selects(scope, test_path)
    )
    assert not missing, f"{mode} hooks cascade misses protected-path guard tests: {missing}"
