"""Architectural test: translate_model called at all terminal --model sites."""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_all_backends_have_translate_model() -> None:
    for name, cls in BACKEND_REGISTRY.items():
        assert hasattr(cls, "translate_model"), f"{name} backend missing translate_model"
        assert callable(getattr(cls, "translate_model")), (
            f"{name} backend translate_model is not callable"
        )


def _get_terminal_model_methods(cls: type) -> list[str]:
    """Find methods that directly reference FLAGS.MODEL in their AST."""
    terminal_methods: list[str] = []
    for method_name in dir(cls):
        if method_name.startswith("_"):
            continue
        method = getattr(cls, method_name, None)
        if not callable(method):
            continue
        try:
            source = inspect.getsource(method)
        except (TypeError, OSError):
            continue
        try:
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "MODEL":
                if isinstance(node.value, ast.Name) and node.value.id.endswith("Flags"):
                    terminal_methods.append(method_name)
                    break
    return terminal_methods


def test_translate_model_called_at_terminal_model_sites() -> None:
    for backend_name, cls in BACKEND_REGISTRY.items():
        terminal_methods = _get_terminal_model_methods(cls)
        assert terminal_methods, f"{backend_name} has no terminal MODEL sites (unexpected)"

        for method_name in terminal_methods:
            method = getattr(cls, method_name)
            source = inspect.getsource(method)
            tree = ast.parse(textwrap.dedent(source))
            has_translate_call = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == "translate_model":
                        has_translate_call = True
                        break
            assert has_translate_call, (
                f"{backend_name}.{method_name} appends --model but does not call "
                f"self.translate_model"
            )
