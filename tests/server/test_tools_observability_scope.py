"""Tests for observability scope completeness in tool handlers using bound_contextvars."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.server.conftest import assert_all_logs_carry_context

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_TOOLS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "server" / "tools"
)
_BOUND_CTX_FILES = [
    _TOOLS_DIR / "tools_status.py",
    _TOOLS_DIR / "tools_pr_ops.py",
    _TOOLS_DIR / "tools_github.py",
]


def _uses_bound_contextvars(func_node: ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(func_node):
        if isinstance(child, ast.With):
            for item in child.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "bound_contextvars"
                ):
                    return True
    return False


def _try_except_is_inside_with_body(func_node: ast.AsyncFunctionDef) -> bool:
    # Use a manual queue instead of ast.walk to avoid descending into nested
    # AsyncFunctionDef/FunctionDef nodes, which could produce false positives.
    queue = list(ast.iter_child_nodes(func_node))
    while queue:
        child = queue.pop()
        if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if isinstance(child, ast.With):
            for item in child.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "bound_contextvars"
                ):
                    if not any(isinstance(stmt, ast.Try) for stmt in child.body):
                        return False
        queue.extend(ast.iter_child_nodes(child))
    return True


class TestKitchenStatusExceptionScope:
    @pytest.mark.anyio
    async def test_kitchen_status_exception_path_carries_context(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """kitchen_status logger.error carries tool= context even when primary op raises."""
        from autoskillit.server.tools.tools_status import kitchen_status

        monkeypatch.setattr(
            "autoskillit.server.version_info",
            MagicMock(side_effect=RuntimeError("mock error")),
        )

        with assert_all_logs_carry_context("tool") as logs:
            await kitchen_status()

        error_logs = [e for e in logs if e.get("log_level") == "error"]
        assert error_logs, "Expected at least one error log record"
        assert all(e.get("tool") == "kitchen_status" for e in error_logs)


class TestGetPrReviewsExceptionScope:
    @pytest.mark.anyio
    async def test_get_pr_reviews_exception_path_carries_context(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """get_pr_reviews logger.error carries tool= context even when subprocess raises."""
        from autoskillit.server.tools.tools_pr_ops import get_pr_reviews

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_pr_ops._run_subprocess",
            AsyncMock(side_effect=RuntimeError("mock error")),
        )

        with assert_all_logs_carry_context("tool") as logs:
            await get_pr_reviews(pr_number=1, cwd="/tmp")

        error_logs = [e for e in logs if e.get("log_level") == "error"]
        assert error_logs, "Expected at least one error log record"
        assert all(e.get("tool") == "get_pr_reviews" for e in error_logs)


class TestFetchGithubIssueExceptionScope:
    @pytest.mark.anyio
    async def test_fetch_github_issue_exception_path_carries_context(self, tool_ctx_kitchen_open):
        """fetch_github_issue logger.error carries tool= context even when fetch_issue raises."""
        from autoskillit.server.tools.tools_github import fetch_github_issue

        mock_github = AsyncMock()
        mock_github.has_token = True
        mock_github.fetch_issue = AsyncMock(side_effect=RuntimeError("mock error"))
        tool_ctx_kitchen_open.github_client = mock_github

        with assert_all_logs_carry_context("tool") as logs:
            await fetch_github_issue(issue_url="https://github.com/owner/repo/issues/1")

        error_logs = [e for e in logs if e.get("log_level") == "error"]
        assert error_logs, "Expected at least one error log record"
        assert all(e.get("tool") == "fetch_github_issue" for e in error_logs)


class TestAllBoundContextvarsToolsHaveExceptionScope:
    @pytest.mark.parametrize(
        "source_path",
        _BOUND_CTX_FILES,
        ids=lambda p: p.name,
    )
    def test_all_bound_contextvars_tools_have_try_except_inside_with(self, source_path: Path):
        """Every async fn using with bound_contextvars() must have try/except inside the with."""
        tree = ast.parse(source_path.read_text())
        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not _uses_bound_contextvars(node):
                continue
            if not _try_except_is_inside_with_body(node):
                violations.append(
                    f"{source_path.name}:{node.lineno} {node.name}() — "
                    "try/except is outside with bound_contextvars() block"
                )
        assert not violations, (
            "Functions with bound_contextvars() must have try/except INSIDE the with block:\n"
            + "\n".join(violations)
        )


class TestAnyAssertionBanned:
    def test_any_assertion_banned_for_contextvars_scope(self):
        """No test_tools_*.py file may assert any(entry.get('tool')...) - use all() instead."""
        test_dir = Path(__file__).parent
        pattern = re.compile(r"""any\(.*\.get\(["']tool["']""")
        violations: list[str] = []
        for test_file in sorted(test_dir.glob("test_tools_*.py")):
            if test_file.name == Path(__file__).name:
                continue
            content = test_file.read_text()
            for lineno, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    violations.append(f"{test_file.name}:{lineno}: {line.strip()}")
        assert not violations, (
            "Found banned any(...get('tool')...) pattern; use all() instead:\n"
            + "\n".join(violations)
        )
