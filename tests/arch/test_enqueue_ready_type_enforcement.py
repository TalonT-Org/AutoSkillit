"""AST enforcement: mutation methods must accept EnqueueReady, not str."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_INIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "execution"
    / "merge_queue"
    / "__init__.py"
)

_MUTATION_METHODS = ("_enqueue_direct", "_enable_auto_merge_direct")


class TestMutationMethodsAcceptEnqueueReady:
    def test_mutation_methods_accept_enqueue_ready_not_str(self):
        source = _INIT_PATH.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name not in _MUTATION_METHODS:
                continue
            args = node.args
            # First positional arg after self
            assert len(args.args) >= 2, f"{node.name} must have at least 2 positional args"
            param = args.args[1]
            annotation = param.annotation
            assert annotation is not None, f"{node.name}: second param must be annotated"
            assert isinstance(annotation, ast.Name), (
                f"{node.name}: second param annotation must be a simple Name, "
                f"got {type(annotation).__name__}"
            )
            assert annotation.id == "EnqueueReady", (
                f"{node.name}: second param must be annotated as EnqueueReady, got {annotation.id}"
            )
