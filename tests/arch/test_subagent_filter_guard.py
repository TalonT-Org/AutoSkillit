"""AST guard: all assistant-record processing must use the subagent filter predicate."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

_GUARDED_FILES = [
    SRC / "execution" / "session" / "_session_model.py",
    SRC / "execution" / "headless" / "_headless_scan.py",
    SRC / "execution" / "headless" / "_headless_evidence.py",
    SRC / "core" / "tool_sequence_analysis.py",
    SRC / "fleet" / "result_parser.py",
]

_PREDICATE_NAMES = {"_is_parent_assistant_record", "_is_parent_assistant"}


@pytest.mark.parametrize("path", _GUARDED_FILES, ids=[p.name for p in _GUARDED_FILES])
def test_assistant_record_branches_use_subagent_filter(path: Path) -> None:
    """Every file that parses 'type == assistant' records must use the predicate."""
    source = path.read_text()
    tree = ast.parse(source)
    body_dump = ast.dump(tree)
    assert any(name in body_dump for name in _PREDICATE_NAMES), (
        f"{path.name} processes assistant NDJSON records but does not call "
        f"_is_parent_assistant_record or _is_parent_assistant — "
        f"subagent records will contaminate results"
    )


_REQUIRED_CHECKS = ["subagent_type", "<synthetic>"]


def test_predicate_copies_are_structurally_complete() -> None:
    """Both IL-0 and IL-1 predicate copies must check all exclusion conditions."""
    for path in [
        SRC / "execution" / "session" / "_session_model.py",
        SRC / "core" / "tool_sequence_analysis.py",
    ]:
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in _PREDICATE_NAMES:
                body_dump = ast.dump(node)
                for check in _REQUIRED_CHECKS:
                    assert check in body_dump, (
                        f"{path.name}:{node.name} must check '{check}' — "
                        f"incomplete predicate allows contamination"
                    )
                break
        else:
            pytest.fail(f"No predicate function found in {path.name}")
