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


def _imports_predicate(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "is_parent_assistant_record" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_parent_assistant_predicate_has_one_complete_definition() -> None:
    tree = ast.parse(AUTHORITY.read_text(encoding="utf-8"))
    definitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "is_parent_assistant_record"
    ]

    assert len(definitions) == 1
    definition_dump = ast.dump(definitions[0])
    for exclusion in ("subagent_type", "<synthetic>"):
        assert exclusion in definition_dump


def test_turn_iterator_calls_the_canonical_predicate() -> None:
    tree = ast.parse(AUTHORITY.read_text(encoding="utf-8"))
    iterator = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "iter_merged_assistant_turns"
    )
    assert "is_parent_assistant_record" in ast.dump(iterator)


def test_analyzer_reexports_the_canonical_predicate() -> None:
    tree = ast.parse(ANALYZER.read_text(encoding="utf-8"))
    assert _imports_predicate(tree)
    assert "is_parent_assistant_record" in ast.dump(tree)


@pytest.mark.parametrize("path", _CONSUMERS, ids=[path.name for path in _CONSUMERS])
def test_consumers_import_the_canonical_predicate(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert _imports_predicate(tree), f"{path.name} must import the canonical predicate"
    assert "is_parent_assistant_record" in ast.dump(tree), (
        f"{path.name} must use the canonical predicate"
    )
