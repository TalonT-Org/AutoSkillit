"""Keep protected-path metadata admission tied to evaluated command provenance."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPO_ROOT / "src" / "autoskillit"
_TESTS_ROOT = _REPO_ROOT / "tests"
_FLAGS_PATH = "src/autoskillit/hooks/_classification/_flags.py"
_INTERPRETERS_PATH = "src/autoskillit/hooks/_classification/_interpreters.py"
_METADATA_ADMISSION = "is_allowed_protected_path_metadata_command"
_PROVENANCE_PROJECTION = "all_evaluated_segments_with_provenance"


def _relative_path(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _python_paths(*roots: Path) -> tuple[Path, ...]:
    return tuple(path for root in roots for path in sorted(root.rglob("*.py")))


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


class _OwnerTrackingVisitor(ast.NodeVisitor):
    """Base visitor that tracks the enclosing function/async-function owner.

    Subclasses implement ``_visit_function_body`` to inspect the function
    node (e.g. record arguments, observe calls). ``_owners[-1]`` is the
    immediate enclosing function name, or ``None`` at module scope.
    """

    def __init__(self) -> None:
        self._owners: list[str | None] = [None]

    def _visit_function_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Hook: subclasses inspect the function node while it is the current owner."""  # noqa: ARG002

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._owners.append(node.name)
        self._visit_function_body(node)
        self.generic_visit(node)
        self._owners.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


class _NamedCallVisitor(_OwnerTrackingVisitor):
    def __init__(self, call_name: str) -> None:
        super().__init__()
        self._call_name = call_name
        self.calls: list[tuple[str | None, int, ast.expr]] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _call_name(node) == self._call_name and node.args:
            self.calls.append((self._owners[-1], node.lineno, node.args[0]))
        self.generic_visit(node)


class _ListAnnotationVisitor(_OwnerTrackingVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.names: dict[str | None, set[str]] = {}

    def _record(self, name: str, annotation: ast.expr | None) -> None:
        if annotation is not None and _is_list_annotation(annotation):
            self.names.setdefault(self._owners[-1], set()).add(name)

    def _visit_function_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self._record(argument.arg, argument.annotation)
        if node.args.vararg is not None:
            self._record(node.args.vararg.arg, node.args.vararg.annotation)
        if node.args.kwarg is not None:
            self._record(node.args.kwarg.arg, node.args.kwarg.annotation)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._record(node.target.id, node.annotation)
        self.generic_visit(node)


def _is_list_annotation(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id == "list"
    return (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and (annotation.value.id == "list")
    )


def _annotation_root_names(annotation: ast.expr) -> set[str]:
    """Return the set of bare-name ids referenced by an annotation expression.

    Walks through Optional / Union / Subscript / Attribute wrappers so that
    ``Foo | None``, ``list[Foo]``, and ``Optional[list[Foo]]`` all surface
    ``Foo``. Type aliases and renamed imports are still resolved by name, so
    callers should compare against the canonical symbol name(s) they expect.
    """
    names: set[str] = set()
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _metadata_admission_calls(
    paths: tuple[Path, ...],
) -> list[tuple[str, str | None, int, ast.expr, set[str]]]:
    calls: list[tuple[str, str | None, int, ast.expr, set[str]]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        call_visitor = _NamedCallVisitor(_METADATA_ADMISSION)
        call_visitor.visit(tree)
        annotation_visitor = _ListAnnotationVisitor()
        annotation_visitor.visit(tree)
        calls.extend(
            (
                _relative_path(path),
                owner,
                lineno,
                argument,
                annotation_visitor.names.get(owner, set()),
            )
            for owner, lineno, argument in call_visitor.calls
        )
    return calls


def _is_bare_list(argument: ast.expr, list_annotated_names: set[str]) -> bool:
    if isinstance(argument, (ast.List, ast.ListComp)):
        return True
    if isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name):
        return argument.func.id == "list"
    return isinstance(argument, ast.Name) and argument.id in list_annotated_names


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, f"expected one {name} definition, found {len(matches)}"
    return matches[0]


def _call_sites_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def test_metadata_admission_accepts_provenance_segments_at_its_sole_production_callsite() -> None:
    flags_tree = ast.parse((_REPO_ROOT / _FLAGS_PATH).read_text(encoding="utf-8"))
    admission = _find_function(flags_tree, _METADATA_ADMISSION)

    assert admission.args.args[0].annotation is not None
    assert "EvaluatedSegment" in _annotation_root_names(admission.args.args[0].annotation)

    production_calls = _metadata_admission_calls(_python_paths(_SOURCE_ROOT))
    assert {(path, owner) for path, owner, _, _, _ in production_calls} == {
        (_FLAGS_PATH, "command_has_blocked_protected_path_read")
    }

    guard = _find_function(flags_tree, "command_has_blocked_protected_path_read")
    provenance_bindings = {
        target.id
        for node in ast.walk(guard)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and _call_name(node.value) == _PROVENANCE_PROJECTION
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert provenance_bindings, "the guard must bind evaluated-segment provenance before admission"

    provenance_calls = [
        call
        for loop in ast.walk(guard)
        if isinstance(loop, ast.For)
        and isinstance(loop.iter, ast.Name)
        and loop.iter.id in provenance_bindings
        and isinstance(loop.target, ast.Name)
        for call in _call_sites_named(loop, _METADATA_ADMISSION)
        if isinstance(call.args[0], ast.Name) and call.args[0].id == loop.target.id
    ]
    assert provenance_calls, (
        "metadata admission must receive the EvaluatedSegment yielded by "
        "all_evaluated_segments_with_provenance"
    )


def test_metadata_admission_has_no_bare_list_or_list_str_callers() -> None:
    calls = _metadata_admission_calls(_python_paths(_SOURCE_ROOT, _TESTS_ROOT))
    bare_list_calls = [
        f"{path}:{lineno}"
        for path, _, lineno, argument, list_annotated_names in calls
        if _is_bare_list(argument, list_annotated_names)
    ]

    assert not bare_list_calls, (
        "metadata admission accepts EvaluatedSegment provenance, never a bare list or "
        "list[str] value:\n" + "\n".join(bare_list_calls)
    )


def test_evaluated_segments_are_constructed_only_by_the_provenance_projection() -> None:
    constructor_sites: set[tuple[str, str | None]] = set()
    for path in _python_paths(_SOURCE_ROOT, _TESTS_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = _NamedCallVisitor("EvaluatedSegment")
        visitor.visit(tree)
        constructor_sites.update((_relative_path(path), owner) for owner, _, _ in visitor.calls)

    assert constructor_sites == {
        (_INTERPRETERS_PATH, _PROVENANCE_PROJECTION),
    }, (
        "EvaluatedSegment construction is the tokenizer projection authority; callers must "
        "obtain instances through all_evaluated_segments_with_provenance"
    )
