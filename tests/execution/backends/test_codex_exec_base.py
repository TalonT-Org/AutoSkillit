"""Tests for _codex_exec_base shared command preamble factory."""

from __future__ import annotations

import ast
import inspect

import pytest

from autoskillit.execution.backends.codex import CodexBackend, _codex_exec_base

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexExecBase:
    def test_returns_expected_preamble(self) -> None:
        result = _codex_exec_base(sandbox="workspace-write")
        assert result == [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "-c",
            "features.image_generation=false",
        ]

    def test_with_approval_never(self) -> None:
        result = _codex_exec_base(sandbox="workspace-write", approval="never")
        assert "-a" in result
        idx = result.index("-a")
        assert result[idx + 1] == "never"

    def test_without_json(self) -> None:
        result = _codex_exec_base(sandbox="read-only", json=False)
        assert "--json" not in result

    def test_with_extra_overrides(self) -> None:
        result = _codex_exec_base(sandbox="read-only", extra_overrides=["web_search=disabled"])
        config_indices = [i for i, v in enumerate(result) if v == "-c"]
        assert len(config_indices) == 2
        assert result[config_indices[0] + 1] == "web_search=disabled"
        assert result[config_indices[1] + 1] == "features.image_generation=false"


class TestNoRawCodexExecListLiteral:
    pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

    def test_no_raw_codex_exec_list_literal_in_codex_backend(self) -> None:
        source = inspect.getsource(CodexBackend)
        tree = ast.parse(source)

        class_body = tree.body[0]
        assert isinstance(class_body, ast.ClassDef)

        for node in ast.walk(class_body):
            if not isinstance(node, ast.List):
                continue
            elts = node.elts
            if len(elts) < 2:
                continue
            first_two = []
            for e in elts[:2]:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    first_two.append(e.value)
            if first_two == ["codex", "exec"]:
                func_name = "<unknown>"
                for parent in ast.walk(class_body):
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for child in ast.walk(parent):
                            if child is node:
                                func_name = parent.name
                                break
                raise AssertionError(
                    f"Raw ['codex', 'exec', ...] list literal found in "
                    f"CodexBackend.{func_name}. Use _codex_exec_base() instead."
                )
