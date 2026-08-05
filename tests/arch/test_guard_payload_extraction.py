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

# Guards not yet migrated to parse_hook_command. Must shrink to empty as guards
# migrate — a stale entry (a name no longer present on disk, or one that no
# longer reads a tracked key) fails the same as a missing migration.
EXEMPT: frozenset[str] = frozenset()


class _TrackedTooInputReadVisitor(ast.NodeVisitor):
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
        return isinstance(node, ast.Name) and node.id == "tool_input"

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
    visitor = _TrackedTooInputReadVisitor()
    visitor.visit(tree)
    return visitor.hits


def test_exempt_basenames_are_live_guard_files() -> None:
    """Every EXEMPT entry must name a file that still exists under guards/."""
    live = {path.name for path in _GUARDS_DIR.glob("*.py")}
    stale = EXEMPT - live
    assert not stale, (
        f"EXEMPT references guard files no longer on disk (stale entry — "
        f"remove it): {sorted(stale)}"
    )


def test_no_direct_tool_input_command_cwd_reads_outside_exempt() -> None:
    """No guard outside EXEMPT reads tool_input["cmd"/"command"/"cwd"] directly.

    Fails immediately for every unmigrated guard when EXEMPT still lists it;
    fails for any *new* guard that hand-rolls extraction without ever being
    added to EXEMPT. The shared path is hooks/_hook_payload.py — one level up
    from guards/, so it is never itself scanned by this glob.
    """
    violations: list[str] = []
    for path in sorted(_GUARDS_DIR.glob("*.py")):
        if path.name in EXEMPT:
            continue
        hits = _scan_guard(path)
        for lineno, key in hits:
            violations.append(f"{path.name}:{lineno} — direct tool_input[{key!r}] read")

    assert not violations, (
        "Guards reading tool_input cmd/command/cwd keys directly instead of via "
        "hooks/_hook_payload.py's parse_hook_command:\n" + "\n".join(violations)
    )


def test_exempt_entries_actually_need_the_exemption() -> None:
    """Every EXEMPT entry must still contain a tracked-key read — no stale exemptions."""
    unnecessary: list[str] = []
    for name in sorted(EXEMPT):
        path = _GUARDS_DIR / name
        if not path.is_file():
            continue  # covered by test_exempt_basenames_are_live_guard_files
        if not _scan_guard(path):
            unnecessary.append(name)

    assert not unnecessary, (
        "EXEMPT lists guards with no remaining direct tool_input cmd/command/cwd "
        f"read — remove the now-unnecessary exemption: {unnecessary}"
    )
