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

    Fixed-point scoped visitor: first discovers which variables hold session type
    values (from os.environ.get reads), then propagates through derived assignments
    until convergence, then collects strings from Compare nodes and SESSION_TYPE
    frozenset constants involving those variables.

    Issue #5121 (T19) hardening rationale: the deleted get_session_type wrapper
    had three call-site shapes that would otherwise silently bypass the unified
    hook_session_shape() API:

    1. Direct call: ``get_session_type()`` — caught at the import site by
       ``test_no_guard_imports_deleted_session_class_wrappers`` (any direct
       call would ImportError at runtime since the wrapper is deleted, so
       no AST-level detection is needed here).
    2. ``as`` rebind: ``from _hook_settings import get_session_type as g``
       followed by ``g()`` — caught by walking ImportFrom nodes up front and
       populating ``_forbidden_aliases`` with the bound ``asname`` (or the
       original name when no ``asname`` is supplied).
    3. Tuple destructure: ``headless, tier = hook_session_shape()`` — caught
       by accepting Assign targets shaped as Tuple or List of two Name nodes
       and registering the second element as the session-type variable
       (because the deleted wrapper returned exactly the tier).
    """

    def __init__(self) -> None:
        self.found: set[str] = set()
        self._session_type_vars: set[str] = set()
        # Populated from ImportFrom rebinds so a `get_session_type as g`
        # call site still resolves to the deleted wrapper name.
        self._forbidden_aliases: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self._discover_session_type_vars(node)
        self.generic_visit(node)

    def _discover_session_type_vars(self, module: ast.Module) -> None:
        # Walk ImportFrom nodes pre-emptively so the rest of the visitor
        # can detect rebinds via 'as' without re-walking the import graph
        # at every call site.
        for node in ast.walk(module):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name == "get_session_type":
                    # alias.asname is the bound name in the local namespace;
                    # if absent, the original name is bound directly.
                    self._forbidden_aliases.add(alias.asname or alias.name)

        assigns: list[tuple[str, ast.expr]] = []
        # Destructure pattern accepts Assign targets shaped as Tuple or List
        # of two Name nodes so 'headless, tier = hook_session_shape()'
        # registers both names — the second is the session-type variable
        # and the first is bound unconditionally so the downstream fixed-
        # point propagation can still match direct headless comparisons if
        # any caller ever branches on headless directly.
        for node in ast.walk(module):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            value = node.value
            if isinstance(target, ast.Name):
                assigns.append((target.id, value))
            elif isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2:
                second = target.elts[1]
                if isinstance(second, ast.Name):
                    assigns.append((second.id, value))
                first = target.elts[0]
                if isinstance(first, ast.Name):
                    assigns.append((first.id, value))

        for name, value in assigns:
            if self._is_session_type_env_read(value, frozenset(self._forbidden_aliases)):
                self._session_type_vars.add(name)

        prev_size = -1
        while len(self._session_type_vars) > prev_size:
            prev_size = len(self._session_type_vars)
            for name, value in assigns:
                if name not in self._session_type_vars and self._derives_from_known_var(value):
                    self._session_type_vars.add(name)

    @staticmethod
    def _is_session_type_env_read(
        node: ast.expr, forbidden_aliases: frozenset[str] = frozenset()
    ) -> bool:
        # Canonical hook_session_shape() call is the session-type source.
        # The function returns (headless, tier); the destructure pattern
        # in _discover_session_type_vars extracts the tier name into
        # _session_type_vars.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "hook_session_shape"
        ):
            return True
        # ``as``-rebinding the deleted wrapper surfaces at the call site as
        # ``g()`` which is identical to a direct wrapper call. The
        # forbidden_aliases set is the precomputed set of every local name
        # that resolves to the deleted wrapper via an 'as' rebind.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_aliases
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "AUTOSKILLIT_SESSION_TYPE"
        ):
            return True
        # Match ``os.environ["AUTOSKILLIT_SESSION_TYPE"]`` subscript access —
        # the pre-existing detector only matched the os.environ.get(...) call
        # form, which would miss direct subscript lookups.
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "AUTOSKILLIT_SESSION_TYPE"
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
            if self._is_session_type_env_read(n, frozenset(self._forbidden_aliases)):
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
    missing = valid_values - found_literals
    assert not missing, (
        f"SessionType values not referenced in any hook file: {sorted(missing)}. "
        "A hook may have been refactored to a pattern the scanner no longer recognises."
    )
