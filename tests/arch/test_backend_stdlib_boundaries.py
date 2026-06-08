"""Stdlib-boundary equality tests for unguarded parallel registries.

These tests guard against silent drift between:
1. The fallback frozenset in `write_guard.py`'s `effective_tool_names` IfExp
   and the canonical `CLAUDE_CODE_CAPABILITIES.write_guard_tool_names` constant.
2. String literals compared against `AUTOSKILLIT_SESSION_TYPE` in hook scripts
   and the canonical `SessionType` StrEnum values.

Hook scripts are standalone stdlib-only subprocesses and cannot import from
`autoskillit.*` at runtime, so we parse them via `ast` rather than importing.
"""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_write_guard_fallback_matches_claude_code_capabilities() -> None:
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES, pkg_root

    wg_path = pkg_root() / "hooks" / "guards" / "write_guard.py"
    tree = ast.parse(wg_path.read_text())

    ann_assign = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "effective_tool_names"
        ):
            ann_assign = node
            break

    if ann_assign is None:
        pytest.fail(
            "effective_tool_names AnnAssign not found in write_guard.py — "
            "file may have been restructured"
        )

    if not isinstance(ann_assign.value, ast.IfExp):
        pytest.fail(
            "effective_tool_names value is not an IfExp — "
            "expected a ternary expression with a frozenset fallback"
        )

    orelse = ann_assign.value.orelse
    if not (
        isinstance(orelse, ast.Call)
        and len(orelse.args) == 1
        and isinstance(orelse.args[0], ast.Set)
    ):
        pytest.fail(
            "effective_tool_names IfExp orelse is not a frozenset({...}) call — "
            "expected frozenset with a single Set argument"
        )

    fallback = frozenset(
        elt.value
        for elt in orelse.args[0].elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    )

    expected = CLAUDE_CODE_CAPABILITIES.write_guard_tool_names
    assert fallback == expected, (
        f"write_guard.py fallback frozenset {sorted(fallback)} does not match "
        f"CLAUDE_CODE_CAPABILITIES.write_guard_tool_names {sorted(expected)}"
    )


class _SessionTypeStringVisitor(ast.NodeVisitor):
    """Collects string literals compared against AUTOSKILLIT_SESSION_TYPE values.

    Two-pass scoped visitor: first discovers which variables hold session type
    values (from os.environ.get reads), then only collects strings from Compare
    nodes involving those variables and from SESSION_TYPE-named frozenset constants.
    """

    def __init__(self) -> None:
        self.found: set[str] = set()
        self._session_type_vars: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self._discover_session_type_vars(node)
        self.generic_visit(node)

    def _discover_session_type_vars(self, module: ast.Module) -> None:
        assigns: list[tuple[str, ast.expr]] = []
        for node in ast.walk(module):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                assigns.append((node.targets[0].id, node.value))

        for name, value in assigns:
            if self._is_session_type_env_read(value):
                self._session_type_vars.add(name)

        for name, value in assigns:
            if name not in self._session_type_vars and self._derives_from_known_var(value):
                self._session_type_vars.add(name)

    @staticmethod
    def _is_session_type_env_read(node: ast.expr) -> bool:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "AUTOSKILLIT_SESSION_TYPE"
        ):
            return True
        return False

    def _derives_from_known_var(self, node: ast.expr) -> bool:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self._session_type_vars
        ):
            return True
        return False

    def _involves_session_type(self, node: ast.Compare) -> bool:
        for n in (node.left, *node.comparators):
            if isinstance(n, ast.Name) and n.id in self._session_type_vars:
                return True
            if self._is_session_type_env_read(n):
                return True
        return False

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._involves_session_type(node):
            for val in (node.left, *node.comparators):
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    self.found.add(val.value)
                elif isinstance(val, (ast.Tuple, ast.Set)):
                    for elt in val.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            self.found.add(elt.value)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and "SESSION_TYPE" in node.targets[0].id.upper()
        ):
            self._collect_frozenset_strings(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Name)
            and "SESSION_TYPE" in node.target.id.upper()
            and node.value is not None
        ):
            self._collect_frozenset_strings(node.value)
        self.generic_visit(node)

    def _collect_frozenset_strings(self, value: ast.expr) -> None:
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Set)
        ):
            for elt in value.args[0].elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    self.found.add(elt.value)


def test_session_type_hook_strings_match_enum() -> None:
    from autoskillit.core import pkg_root
    from autoskillit.core.types._type_enums import SessionType

    hooks_root = pkg_root() / "hooks"
    found_literals: set[str] = set()

    for py_file in sorted(hooks_root.rglob("*.py")):
        try:
            source = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        if "AUTOSKILLIT_SESSION_TYPE" not in source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        visitor = _SessionTypeStringVisitor()
        visitor.visit(tree)
        found_literals |= visitor.found

    assert found_literals, (
        "No session type string literals found in any hook file — scanner may need updating"
    )

    valid_values = {m.value for m in SessionType}
    unrecognized = found_literals - valid_values
    assert not unrecognized, (
        f"Unrecognized session type literals in hook files: {sorted(unrecognized)}. "
        f"Valid SessionType values: {sorted(valid_values)}"
    )
