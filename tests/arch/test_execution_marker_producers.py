"""Keep execution markers limited to the run-skill attestation producer."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_run_skill_finalize_is_the_only_execution_marker_producer() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source_root = repository_root / "src/autoskillit"
    producers: list[tuple[Path, str | None]] = []

    for source_path in source_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if called_name != "execution_marker":
                continue
            label = next(
                (
                    keyword.value.value
                    for keyword in node.keywords
                    if keyword.arg == "label"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            if label is None and len(node.args) > 2:
                label_arg = node.args[2]
                if isinstance(label_arg, ast.Constant) and isinstance(label_arg.value, str):
                    label = label_arg.value
            producers.append((source_path.relative_to(repository_root), label))

    assert producers == [
        (Path("src/autoskillit/server/tools/tools_execution/_run_skill_finalize.py"), "run-skill")
    ]
