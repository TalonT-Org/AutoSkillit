"""Guards and hooks extract tool input facts through shared runtime helpers.

``hooks/_runtime/_hook_payload.py`` holds the sanctioned extraction helpers;
this test makes regressions back to direct ``tool_input`` reads fail.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARDS_DIR = _REPO_ROOT / "src" / "autoskillit" / "hooks" / "guards"
_HOOKS_DIR = _REPO_ROOT / "src" / "autoskillit" / "hooks"

# The exact tool_input key names that must be extracted exclusively through
# hooks/_hook_payload.py (parse_hook_command / extract_apply_patch_text).
_TRACKED_KEYS: frozenset[str] = frozenset({"cmd", "command", "cwd"})
_EDIT_TARGET_KEYS: frozenset[str] = frozenset({"file_path"})


class _TrackedToolInputReadVisitor(ast.NodeVisitor):
    """Collects (lineno, key) for every direct tool_input["cmd"/"command"/"cwd"]
    subscript or .get("cmd"/"command"/"cwd", ...) call found in the module.

    Scoped to reads whose receiver is a variable literally named
    ``tool_input`` — the consistent shape every guard used before migration
    (``tool_input = data.get("tool_input", {})`` followed by
    ``tool_input.get("command", ...)`` / ``tool_input["cwd"]``).
    """

    def __init__(self, tracked_keys: frozenset[str]) -> None:
        self.tracked_keys = tracked_keys
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
            if isinstance(key_node, ast.Constant) and key_node.value in self.tracked_keys:
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
            and node.args[0].value in self.tracked_keys
        ):
            self.hits.append((node.lineno, str(node.args[0].value)))
        self.generic_visit(node)


def _scan_guard(path: Path, tracked_keys: frozenset[str]) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    visitor = _TrackedToolInputReadVisitor(tracked_keys)
    visitor.visit(tree)
    return visitor.hits


def test_no_direct_tool_input_command_cwd_reads() -> None:
    """No guard reads tool_input["cmd"/"command"/"cwd"] directly.

    The shared path is hooks/_hook_payload.py — one level up from guards/, so
    it is never itself scanned by this glob.
    """
    violations: list[str] = []
    for path in sorted(_GUARDS_DIR.glob("*.py")):
        hits = _scan_guard(path, _TRACKED_KEYS)
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

    assert _scan_guard(source, _TRACKED_KEYS) == [(1, "cmd"), (2, "command")]


def test_no_direct_tool_input_file_path_reads() -> None:
    """Registered hooks and runtime helpers use the shared payload extractor."""
    from tests._hook_channel_scan import all_registered_hook_defs, registered_scripts

    scripts = registered_scripts(all_registered_hook_defs())
    paths = {_HOOKS_DIR / script for script in scripts}
    paths.update(
        path for path in (_HOOKS_DIR / "_runtime").glob("*.py") if path.name != "_hook_payload.py"
    )
    violations: list[str] = []
    for path in sorted(paths):
        for lineno, key in _scan_guard(path, _EDIT_TARGET_KEYS):
            violations.append(
                f"{path.relative_to(_REPO_ROOT)}:{lineno} — direct tool_input[{key!r}] read"
            )

    assert not violations, (
        "Registered hooks and hook runtime modules must use "
        "hooks/_runtime/_hook_payload.py for file_path extraction:\n" + "\n".join(violations)
    )


def test_nested_data_tool_input_file_path_reads_are_detected(tmp_path: Path) -> None:
    source = tmp_path / "nested-file-path.py"
    source.write_text(
        "a = data['tool_input']['file_path']\nb = data.get('tool_input', {}).get('file_path')\n",
        encoding="utf-8",
    )

    assert _scan_guard(source, _EDIT_TARGET_KEYS) == [(1, "file_path"), (2, "file_path")]
