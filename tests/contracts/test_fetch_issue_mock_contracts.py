"""Contract test: all fetch_issue mock return values must include a 'state' and 'body' field."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

TESTS_DIR = Path(__file__).parents[2] / "tests"


_REQUIRED_FETCH_ISSUE_KEYS = ("state", "body")


def _literal_string_key_values(dict_node: ast.Dict) -> dict[str, ast.expr]:
    return {
        key.value: value
        for key, value in zip(dict_node.keys, dict_node.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _missing_key_diagnostics(
    test_file: Path, lineno: int, label: str, keys: dict[str, ast.expr]
) -> list[str]:
    return [
        f"{test_file.name}:{lineno} {label} missing '{key}' key"
        for key in _REQUIRED_FETCH_ISSUE_KEYS
        if key not in keys
    ]


def _named_dictionary_definitions(tree: ast.AST) -> dict[str, tuple[int, dict[str, ast.expr]]]:
    definitions: dict[str, tuple[int, dict[str, ast.expr]]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            definitions[node.targets[0].id] = (
                node.lineno,
                _literal_string_key_values(node.value),
            )
    return definitions


def _fetch_issue_mock_values(node: ast.Assign) -> list[tuple[ast.expr, str]]:
    target = node.targets[0] if node.targets else None
    if (
        isinstance(target, ast.Attribute)
        and target.attr == "return_value"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "fetch_issue"
    ):
        return [(node.value, ".fetch_issue.return_value =")]
    if (
        isinstance(target, ast.Attribute)
        and target.attr == "fetch_issue"
        and isinstance(node.value, ast.Call)
    ):
        return [
            (keyword.value, "fetch_issue AsyncMock return_value")
            for keyword in node.value.keywords
            if keyword.arg == "return_value"
        ]
    return []


def _mock_value_failures(
    value_node: ast.expr,
    lineno: int,
    label: str,
    variable_dicts: dict[str, tuple[int, dict[str, ast.expr]]],
    test_file: Path,
) -> list[str]:
    if isinstance(value_node, ast.Dict):
        keys = _literal_string_key_values(value_node)
        success = keys.get("success")
        if isinstance(success, ast.Constant) and success.value is True:
            return _missing_key_diagnostics(test_file, lineno, label, keys)
    elif isinstance(value_node, ast.Name) and value_node.id in variable_dicts:
        definition_line, keys = variable_dicts[value_node.id]
        variable_label = f"variable '{value_node.id}' (defined at line {definition_line})"
        return _missing_key_diagnostics(test_file, lineno, variable_label, keys)
    return []


def _collect_failures(test_file: Path) -> list[str]:
    source = test_file.read_text()
    tree = ast.parse(source)
    variable_dicts = _named_dictionary_definitions(tree)
    failures: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for value, label in _fetch_issue_mock_values(node):
                failures.extend(
                    _mock_value_failures(value, node.lineno, label, variable_dicts, test_file)
                )

    return failures


def test_collect_failures_preserves_inline_and_named_dictionary_guards(tmp_path: Path) -> None:
    test_file = tmp_path / "synthetic.py"
    test_file.write_text(
        'api.fetch_issue.return_value = {"success": True}\n'
        'api.fetch_issue.return_value = {"success": False}\n'
        "api.fetch_issue = AsyncMock(return_value=later)\n"
        'later = {"success": False}\n'
    )

    assert _collect_failures(test_file) == [
        "synthetic.py:1 .fetch_issue.return_value = missing 'state' key",
        "synthetic.py:1 .fetch_issue.return_value = missing 'body' key",
        "synthetic.py:3 variable 'later' (defined at line 4) missing 'state' key",
        "synthetic.py:3 variable 'later' (defined at line 4) missing 'body' key",
    ]


def test_all_fetch_issue_mocks_include_state_and_body_field() -> None:
    """Every fetch_issue mock return value must include a \'state\' and \'body\' key.

    Prevents future tests from regressing to state-blind or body-blind mocks, which caused the
    food-truck re-dispatch bug to go undetected across 7 test files.
    """
    test_files = [f for f in TESTS_DIR.rglob("test_*.py") if "fetch_issue" in f.read_text()]
    assert test_files, "No test files with fetch_issue found — check TESTS_DIR path"

    all_failures = []
    for test_file in test_files:
        all_failures.extend(_collect_failures(test_file))

    assert not all_failures, "fetch_issue mocks missing 'state' and/or 'body' key:\n" + "\n".join(
        all_failures
    )
