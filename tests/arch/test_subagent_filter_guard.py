"""AST guard: parent-assistant filtering has one shared authority."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
AUTHORITY = SRC / "_parent_assistant_turns.py"
ANALYZER = SRC / "core" / "pipeline" / "tool_sequence_analysis.py"
_CONSUMERS = [
    SRC / "execution" / "session" / "_session_model.py",
    SRC / "execution" / "headless" / "_headless_recovery.py",
    SRC / "execution" / "headless" / "_headless_evidence.py",
    SRC / "execution" / "session_log" / "session_log.py",
    SRC / "fleet" / "result_parser.py",
    SRC / "hooks" / "guards" / "fabricated_completion_guard.py",
]

_PREDICATE_NAME = "is_parent_assistant_record"


def _imports_predicate(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == _PREDICATE_NAME for alias in node.names)
        for node in ast.walk(tree)
    )


def _calls_predicate(tree: ast.AST, *, name: str = _PREDICATE_NAME) -> bool:
    """Return True iff ``tree`` contains a call expression to ``name``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False


def _predicates(tree: ast.AST, *, name: str) -> list[ast.FunctionDef]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    ]


def _has_string_constant(node: ast.AST, value: str) -> bool:
    """Return True iff ``node`` (or any descendant) contains an ast.Constant
    with the given string value.

    String-only matching (rather than ``ast.dump`` substring containment) so a
    substring present only as a docstring or unrelated literal does not satisfy
    the assertion.
    """
    if isinstance(node, ast.Constant) and node.value == value:
        return True
    return any(_has_string_constant(child, value) for child in ast.iter_child_nodes(node))


def test_parent_assistant_predicate_has_one_complete_definition() -> None:
    tree = ast.parse(AUTHORITY.read_text(encoding="utf-8"))
    definitions = _predicates(tree, name=_PREDICATE_NAME)

    assert len(definitions) == 1, (
        f"Expected exactly one definition of {_PREDICATE_NAME}, found {len(definitions)}."
    )
    definition = definitions[0]
    assert _has_string_constant(definition, "subagent_type"), (
        f"{_PREDICATE_NAME} must exclude Task subagent records by checking "
        "subagent_type as a constant key, not a substring of any docstring."
    )
    assert _has_string_constant(definition, "<synthetic>"), (
        f"{_PREDICATE_NAME} must exclude synthetic assistant messages by "
        "comparing message.model to the <synthetic> literal."
    )


def test_turn_iterator_calls_the_canonical_predicate() -> None:
    tree = ast.parse(AUTHORITY.read_text(encoding="utf-8"))
    iterator = next(
        iter(_predicates(tree, name="iter_merged_assistant_turns")),
    )
    assert _calls_predicate(iterator), (
        "iter_merged_assistant_turns() must call is_parent_assistant_record() — "
        "substring containment of the predicate name in the function body is "
        "insufficient (a docstring would falsely satisfy it)."
    )


def test_analyzer_reexports_the_canonical_predicate() -> None:
    tree = ast.parse(ANALYZER.read_text(encoding="utf-8"))
    assert _imports_predicate(tree)
    assert _calls_predicate(tree), (
        f"{ANALYZER.name} must call {_PREDICATE_NAME} to re-export it through "
        "the pipeline namespace."
    )


@pytest.mark.parametrize("path", _CONSUMERS, ids=[path.name for path in _CONSUMERS])
def test_consumers_import_the_canonical_predicate(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert _imports_predicate(tree), f"{path.name} must import the canonical predicate"
    assert _calls_predicate(tree), (
        f"{path.name} must invoke the canonical predicate; importing it without "
        "calling it leaves the duplicate-filter risk intact."
    )
