"""Standing invariant: guards extract command/cwd facts via the shared module.

Every command-inspecting guard hand-rolled its own ``json.loads(stdin)`` ->
``tool_input.get(...)`` parsing before this workstream — the gap that let
guards diverge on Bash-vs-run_cmd field names and cwd sourcing (see the
Rectify plan's Related Issues). ``hooks/_hook_payload.py``'s
``parse_hook_command`` is now the sole sanctioned extraction point; this test
makes a regression back to direct ``tool_input`` reads a standing failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARDS_DIR = _REPO_ROOT / "src" / "autoskillit" / "hooks" / "guards"

# The exact tool_input key names that must be extracted exclusively through
# hooks/_hook_payload.py (parse_hook_command / extract_apply_patch_text).
_TRACKED_KEYS: frozenset[str] = frozenset({"cmd", "command", "cwd"})


class _TrackedToolInputReadVisitor(ast.NodeVisitor):
    """Collects (lineno, key) for every direct tool_input["cmd"/"command"/"cwd"]
    subscript or .get("cmd"/"command"/"cwd", ...) call found in the module.

    Scoped to reads whose receiver is a variable literally named
    ``tool_input`` — the consistent shape every guard used before migration
    (``tool_input = data.get("tool_input", {})`` followed by
    ``tool_input.get("command", ...)`` / ``tool_input["cwd"]``).
    """

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    @staticmethod
    def _is_tool_input_name(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "tool_input"
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "data"
            and isinstance(node.slice, ast.Constant)
        ):
            return node.slice.value == "tool_input"
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "data"
            and bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "tool_input"
        )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_tool_input_name(node.value):
            key_node = node.slice
            if isinstance(key_node, ast.Constant) and key_node.value in _TRACKED_KEYS:
                self.hits.append((node.lineno, str(key_node.value)))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and self._is_tool_input_name(func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _TRACKED_KEYS
        ):
            self.hits.append((node.lineno, str(node.args[0].value)))
        self.generic_visit(node)


def _scan_guard(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    visitor = _TrackedToolInputReadVisitor()
    visitor.visit(tree)
    return visitor.hits


def test_no_direct_tool_input_command_cwd_reads() -> None:
    """No guard reads tool_input["cmd"/"command"/"cwd"] directly.

    The shared path is hooks/_hook_payload.py — one level up from guards/, so
    it is never itself scanned by this glob.
    """
    violations: list[str] = []
    for path in sorted(_GUARDS_DIR.glob("*.py")):
        hits = _scan_guard(path)
        for lineno, key in hits:
            violations.append(f"{path.name}:{lineno} — direct tool_input[{key!r}] read")

    assert not violations, (
        "Guards reading tool_input cmd/command/cwd keys directly instead of via "
        "hooks/_hook_payload.py's parse_hook_command:\n" + "\n".join(violations)
    )


def test_nested_data_tool_input_reads_are_detected(tmp_path: Path) -> None:
    source = tmp_path / "nested.py"
    source.write_text(
        "a = data['tool_input']['cmd']\nb = data.get('tool_input', {}).get('command')\n",
        encoding="utf-8",
    )

    assert _scan_guard(source) == [(1, "cmd"), (2, "command")]
