"""AST regression guard: capability scanners must import and call _strip_doc_fenced_blocks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SCANNER_PATHS = (
    "tests/arch/test_skill_backend_annotations.py",
    "tests/skills/test_skill_commit_discipline.py",
    "tests/arch/test_skill_backend_annotation_accuracy.py",
)


def _file_has_import_and_call(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    has_import = False
    has_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            if "_strip_doc_fenced_blocks" in names:
                has_import = True
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "_strip_doc_fenced_blocks":
                has_call = True
    return has_import and has_call


def test_capability_scanners_use_doc_fence_filter():
    for rel_path in _SCANNER_PATHS:
        full_path = Path(__file__).resolve().parent.parent.parent / rel_path
        assert _file_has_import_and_call(full_path), (
            f"{rel_path} must import and call _strip_doc_fenced_blocks from tests.arch._helpers"
        )
