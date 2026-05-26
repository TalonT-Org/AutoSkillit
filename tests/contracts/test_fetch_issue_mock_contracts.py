"""Contract test: all fetch_issue mock return values must include a \'state\' field."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

TESTS_DIR = Path(__file__).parents[2] / "tests"


def _collect_failures(test_file: Path) -> list[str]:
    source = test_file.read_text()
    tree = ast.parse(source)

    # Track dict literals assigned to named variables (for variable-based mocks)
    variable_dicts: dict[str, tuple[int, bool]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            var_name = node.targets[0].id
            keys = {
                k.value
                for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            variable_dicts[var_name] = (node.lineno, "state" in keys)

    failures = []

    def _check_dict(dict_node: ast.Dict, lineno: int, label: str) -> None:
        keys_and_values = {
            k.value: v
            for k, v in zip(dict_node.keys, dict_node.values)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        success_val = keys_and_values.get("success")
        is_success_true = isinstance(success_val, ast.Constant) and success_val.value is True
        if is_success_true and "state" not in keys_and_values:
            failures.append(f"{test_file.name}:{lineno} {label} missing 'state' key")

    def _check_value(value_node: ast.expr, lineno: int, label: str) -> None:
        if isinstance(value_node, ast.Dict):
            _check_dict(value_node, lineno, label)
        elif isinstance(value_node, ast.Name):
            var_name = value_node.id
            if var_name in variable_dicts:
                var_lineno, has_state = variable_dicts[var_name]
                if not has_state:
                    failures.append(
                        f"{test_file.name}:{lineno} variable '{var_name}' "
                        f"(defined at line {var_lineno}) missing 'state' key"
                    )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0] if node.targets else None

        # Pattern 1: foo.fetch_issue.return_value = {...}
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "return_value"
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "fetch_issue"
        ):
            _check_value(node.value, node.lineno, ".fetch_issue.return_value =")

        # Pattern 2: foo.fetch_issue = AsyncMock(return_value={...})
        if isinstance(target, ast.Attribute) and target.attr == "fetch_issue":
            if isinstance(node.value, ast.Call):
                for kw in node.value.keywords:
                    if kw.arg == "return_value":
                        _check_value(kw.value, node.lineno, "fetch_issue AsyncMock return_value")

    return failures


def test_all_fetch_issue_mocks_include_state_field() -> None:
    """Every fetch_issue mock return value must include a \'state\' key.

    Prevents future tests from regressing to state-blind mocks, which caused the
    food-truck re-dispatch bug to go undetected across 7 test files.
    """
    test_files = [f for f in TESTS_DIR.rglob("test_*.py") if "fetch_issue" in f.read_text()]
    assert test_files, "No test files with fetch_issue found — check TESTS_DIR path"

    all_failures = []
    for test_file in test_files:
        all_failures.extend(_collect_failures(test_file))

    assert not all_failures, "fetch_issue mocks missing 'state' key:\n" + "\n".join(all_failures)
