"""Arch guard: InputSpec type dispatch must be exhaustive (match/assert_never).

Mirrors test_session_type_exhaustive.py — ensures that adding a new member
to VALID_INPUT_SPEC_TYPES without wiring a dispatch branch in
_check_input_contracts or resolve_input_specs fails CI rather than silently
falling through.
"""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


class _MatchWithAssertNeverVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.has_match = False
        self.has_assert_never = False

    def visit_Match(self, node: ast.Match) -> None:
        self.has_match = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "assert_never":
            self.has_assert_never = True
        self.generic_visit(node)


def _get_function_body(source: str, func_name: str) -> list[ast.stmt]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node.body
    msg = f"{func_name} not found"
    raise AssertionError(msg)


def test_check_input_contracts_uses_match_assert_never():
    """_check_input_contracts must use match/assert_never for exhaustive dispatch."""
    from autoskillit.core import paths

    src_file = paths.pkg_root() / "server" / "_guards.py"
    assert src_file.exists(), f"File not found: {src_file}"
    body = _get_function_body(src_file.read_text(), "_check_input_contracts")
    fn_tree = ast.Module(body=body, type_ignores=[])
    visitor = _MatchWithAssertNeverVisitor()
    visitor.visit(fn_tree)
    assert visitor.has_match, (
        "_check_input_contracts must use a 'match' statement for type dispatch"
    )
    assert visitor.has_assert_never, (
        "_check_input_contracts must call assert_never() to guard against "
        "unhandled InputSpecType members"
    )


def test_resolve_input_specs_uses_match_assert_never():
    """resolve_input_specs must use match/assert_never for exhaustive dispatch."""
    from autoskillit.core import paths

    src_file = paths.pkg_root() / "recipe" / "_contracts_manifest.py"
    assert src_file.exists(), f"File not found: {src_file}"
    body = _get_function_body(src_file.read_text(), "resolve_input_specs")
    fn_tree = ast.Module(body=body, type_ignores=[])
    visitor = _MatchWithAssertNeverVisitor()
    visitor.visit(fn_tree)
    assert visitor.has_match, "resolve_input_specs must use a 'match' statement for type dispatch"
    assert visitor.has_assert_never, (
        "resolve_input_specs must call assert_never() to guard against "
        "unhandled InputSpecType members"
    )


def test_yaml_path_input_types_are_covered():
    """Every YAML path-like input type must be a member of VALID_INPUT_SPEC_TYPES."""
    from autoskillit.core import VALID_INPUT_SPEC_TYPES, load_yaml, paths

    yaml_path = paths.pkg_root() / "recipe" / "skill_contracts.yaml"
    assert yaml_path.exists(), f"YAML not found: {yaml_path}"

    raw = load_yaml(yaml_path)

    yaml_types: set[str] = set()
    for skill_data in raw.get("skills", {}).values():
        for inp in skill_data.get("inputs", []):
            yaml_types.add(inp["type"])

    path_like_yaml_types = {
        t
        for t in yaml_types
        if t.startswith("file_")
        or t.startswith("directory_")
        or t.endswith("_path")
        or t.endswith("_path_list")
    }

    unrecognized = path_like_yaml_types - set(VALID_INPUT_SPEC_TYPES)
    assert not unrecognized, (
        f"YAML declares path-like input types not in VALID_INPUT_SPEC_TYPES: "
        f"{sorted(unrecognized)}. Add the new type to both VALID_INPUT_SPEC_TYPES "
        f"and InputSpecType."
    )
