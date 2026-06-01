"""Architectural test: no hardcoded "origin" in git remote operations.

Enforces the clone isolation contract: code that performs git remote operations
(ls-remote, push, fetch, remote get-url) must not hardcode "origin" as the
remote name, since _ensure_origin_isolated rewrites it to a file:// URL.

Two guards:
  1. AST scan of Python files for string literals "origin" passed to git commands.
  2. Shell script lint for origin-before-upstream precedence in remote detection.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"

GIT_REMOTE_COMMANDS = {"ls-remote", "push", "fetch", "remote"}

PYTHON_SCAN_DIRS = (
    SRC_ROOT / "server",
    SRC_ROOT / "hooks",
    SRC_ROOT / "execution" / "headless",
    SRC_ROOT / "recipe",
)

PYTHON_ALLOWLIST: set[tuple[str, str]] = {
    ("_clone_remote.py", "_ensure_origin_isolated"),
    ("_clone_remote.py", "_probe_single_remote"),
    ("_clone_remote.py", "_probe_clone_source_url"),
    ("_clone_remote.py", "_add_or_set_upstream"),
}

SHELL_SCAN_DIR = SRC_ROOT / "recipes" / "scripts"


def _find_enclosing_function(node: ast.AST, tree: ast.Module) -> str:
    """Walk parents to find the enclosing function name."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if child is node and isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return parent.name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for desc in ast.walk(child):
                    if desc is node:
                        return child.name
    return "<module>"


def _is_git_remote_context(node: ast.AST, tree: ast.Module) -> bool:
    """Check if a string "origin" appears in a context that looks like a git command."""
    parent_list = None
    for candidate in ast.walk(tree):
        if isinstance(candidate, ast.List):
            for elt in candidate.elts:
                if elt is node:
                    parent_list = candidate
                    break
    if parent_list is None:
        return False

    for elt in parent_list.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            if elt.value in GIT_REMOTE_COMMANDS or elt.value.startswith("refs/remotes/origin"):
                return True
    return False


class TestNoHardcodedOriginInPython:
    """AST scan: Python files must not pass 'origin' as a remote to git commands."""

    def test_no_origin_in_git_commands(self) -> None:
        violations: list[str] = []

        for scan_dir in PYTHON_SCAN_DIRS:
            if not scan_dir.exists():
                continue
            for py_file in sorted(scan_dir.rglob("*.py")):
                try:
                    tree = ast.parse(py_file.read_text(), filename=str(py_file))
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    if not isinstance(node, ast.Constant):
                        continue
                    if not isinstance(node.value, str):
                        continue
                    if node.value != "origin":
                        continue

                    if not _is_git_remote_context(node, tree):
                        continue

                    fn_name = _find_enclosing_function(node, tree)
                    if (py_file.name, fn_name) in PYTHON_ALLOWLIST:
                        continue

                    rel = py_file.relative_to(SRC_ROOT)
                    violations.append(f"  {rel}:{node.lineno} in {fn_name}()")

        assert not violations, (
            "Hardcoded 'origin' in git remote operations violates the clone isolation contract.\n"
            "Use resolve_remote_name (async) or resolve_clone_remote_name_sync (sync) instead.\n"
            + "\n".join(violations)
        )

    def test_no_refs_remotes_origin_hardcoded(self) -> None:
        """Catch refs/remotes/origin/ string literals that bypass the resolved remote."""
        violations: list[str] = []
        pattern = re.compile(r"refs/remotes/origin")

        for scan_dir in PYTHON_SCAN_DIRS:
            if not scan_dir.exists():
                continue
            for py_file in sorted(scan_dir.rglob("*.py")):
                try:
                    tree = ast.parse(py_file.read_text(), filename=str(py_file))
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    if not isinstance(node, ast.Constant):
                        continue
                    if not isinstance(node.value, str):
                        continue
                    if not pattern.search(node.value):
                        continue

                    fn_name = _find_enclosing_function(node, tree)
                    if (py_file.name, fn_name) in PYTHON_ALLOWLIST:
                        continue

                    rel = py_file.relative_to(SRC_ROOT)
                    violations.append(f"  {rel}:{node.lineno} in {fn_name}()")

        assert not violations, (
            "Hardcoded 'refs/remotes/origin' bypasses the remote resolver.\n"
            "Use f'refs/remotes/{remote}/...' with the resolved remote name.\n"
            + "\n".join(violations)
        )


class TestShellScriptRemotePrecedence:
    """Shell scripts must try upstream before origin in remote detection."""

    @pytest.mark.parametrize(
        "script",
        sorted(SHELL_SCAN_DIR.glob("*.sh")) if SHELL_SCAN_DIR.exists() else [],
        ids=lambda p: p.name,
    )
    def test_no_origin_before_upstream(self, script: Path) -> None:
        content = script.read_text()

        origin_match = re.search(r"git\s+remote\s+get-url\s+origin", content)
        upstream_match = re.search(r"git\s+remote\s+get-url\s+upstream", content)

        if origin_match and upstream_match:
            assert upstream_match.start() < origin_match.start(), (
                f"{script.name}: tries 'origin' before 'upstream' in remote detection. "
                "The clone isolation contract requires upstream-first precedence."
            )

    @pytest.mark.parametrize(
        "script",
        sorted(SHELL_SCAN_DIR.glob("*.sh")) if SHELL_SCAN_DIR.exists() else [],
        ids=lambda p: p.name,
    )
    def test_no_bare_push_origin(self, script: Path) -> None:
        content = script.read_text()

        bare_push = re.search(r"git\s+push\s+(-u\s+)?origin\b", content)

        assert bare_push is None, (
            f"{script.name}:{content[: bare_push.start()].count(chr(10)) + 1}: "
            f"hardcoded 'git push origin' bypasses clone isolation. "
            "Use a resolved $REMOTE variable instead."
        )
