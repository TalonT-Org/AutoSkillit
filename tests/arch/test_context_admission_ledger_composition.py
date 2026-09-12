"""Architecture checks for static context-admission ledger composition."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

LEDGER_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "pipeline"
    / "_context_admission_ledger"
)
_TYPE_CHECKER_SUPPRESSION_RE = re.compile(
    r"#.*(?:\btype\s*:\s*ignore\b|\b(?:mypy|pyright)\s*:)",
    re.IGNORECASE,
)
_ANY_ANNOTATION_RE = re.compile(r"\bAny\b")

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
_ClassLocation = tuple[Path, ast.ClassDef]


def _ledger_modules() -> dict[Path, ast.Module]:
    """Parse only production files belonging to the ledger subpackage."""
    return {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(LEDGER_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _is_ledger_class_reference(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "DefaultContextAdmissionLedger") or (
        isinstance(node, ast.Attribute) and node.attr == "DefaultContextAdmissionLedger"
    )


class _ModuleLevelRebindingVisitor(ast.NodeVisitor):
    """Find facade attribute installation outside any class or function."""

    def __init__(self) -> None:
        self.violations: list[int] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and node.args
            and _is_ledger_class_reference(node.args[0])
        ):
            self.violations.append(node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(
            isinstance(target, ast.Attribute) and _is_ledger_class_reference(target.value)
            for target in node.targets
        ):
            self.violations.append(node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Attribute) and _is_ledger_class_reference(
            node.target.value
        ):
            self.violations.append(node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Attribute) and _is_ledger_class_reference(
            node.target.value
        ):
            self.violations.append(node.lineno)
        self.generic_visit(node)


def _is_not_implemented_raise(node: ast.stmt) -> bool:
    if not isinstance(node, ast.Raise):
        return False
    if isinstance(node.exc, ast.Name):
        return node.exc.id == "NotImplementedError"
    return (
        isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "NotImplementedError"
    )


@pytest.mark.parametrize(
    "expression",
    [
        "DefaultContextAdmissionLedger",
        "ledger_module.DefaultContextAdmissionLedger",
    ],
)
def test_ledger_class_reference_recognizes_direct_and_qualified_names(
    expression: str,
) -> None:
    node = ast.parse(expression, mode="eval").body

    assert _is_ledger_class_reference(node)


def _is_not_implemented_placeholder(function: _FunctionNode) -> bool:
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return len(body) == 1 and _is_not_implemented_raise(body[0])


def _first_positional_parameter(function: _FunctionNode) -> ast.arg | None:
    positional = (*function.args.posonlyargs, *function.args.args)
    return positional[0] if positional else None


def _class_definitions(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _local_imports(path: Path, tree: ast.Module) -> dict[str, tuple[Path, str]]:
    imports: dict[str, tuple[Path, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        package_dir = path.parent
        for _ in range(node.level - 1):
            package_dir = package_dir.parent
        module_path = (
            package_dir / f"{node.module.replace('.', '/')}.py"
            if node.module is not None
            else package_dir / "__init__.py"
        )
        if not module_path.is_file() or LEDGER_DIR not in module_path.parents:
            continue
        for imported in node.names:
            imports[imported.asname or imported.name] = (module_path, imported.name)
    return imports


def _resolve_local_base(
    path: Path,
    base: ast.expr,
    modules: dict[Path, ast.Module],
) -> _ClassLocation | None:
    if not isinstance(base, ast.Name):
        return None
    classes = _class_definitions(modules[path])
    if base.id in classes:
        return path, classes[base.id]
    imported = _local_imports(path, modules[path]).get(base.id)
    if imported is None:
        return None
    imported_path, imported_name = imported
    imported_classes = _class_definitions(modules[imported_path])
    if imported_name not in imported_classes:
        return None
    return imported_path, imported_classes[imported_name]


def _final_ledger_ancestry(modules: dict[Path, ast.Module]) -> list[_ClassLocation]:
    facade_path = LEDGER_DIR / "__init__.py"
    facade_classes = _class_definitions(modules[facade_path])
    facade = facade_classes.get("DefaultContextAdmissionLedger")
    assert facade is not None, "DefaultContextAdmissionLedger must remain the public facade"

    ancestry: list[_ClassLocation] = []
    current = facade_path, facade
    seen: set[tuple[Path, str]] = set()
    while True:
        identity = current[0], current[1].name
        assert identity not in seen, "ledger implementation base chain must not cycle"
        seen.add(identity)
        ancestry.append(current)
        local_bases = [
            resolved
            for base in current[1].bases
            if (resolved := _resolve_local_base(current[0], base, modules)) is not None
        ]
        unresolved_bases = [
            ast.unparse(base)
            for base in current[1].bases
            if _resolve_local_base(current[0], base, modules) is None
            and not (isinstance(base, ast.Name) and base.id == "object")
        ]
        assert not unresolved_bases, (
            f"{current[0].name}:{current[1].name} has a non-local base: {unresolved_bases}"
        )
        assert len(local_bases) <= 1, (
            f"{current[0].name}:{current[1].name} must use a linear local base chain"
        )
        if not local_bases:
            return ancestry
        current = local_bases[0]


def _decorator_names(function: _FunctionNode) -> set[str]:
    names: set[str] = set()
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Name):
            names.add(decorator.id)
        elif isinstance(decorator, ast.Attribute):
            names.add(decorator.attr)
    return names


def _function_contexts(tree: ast.Module) -> Iterator[tuple[_FunctionNode, bool]]:
    methods: dict[int, bool] = {}
    for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        for statement in class_node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[id(statement)] = "staticmethod" not in _decorator_names(statement)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, methods.get(id(node), False)


def _function_parameters(function: _FunctionNode) -> Iterator[ast.arg]:
    yield from function.args.posonlyargs
    yield from function.args.args
    yield from function.args.kwonlyargs
    if function.args.vararg is not None:
        yield function.args.vararg
    if function.args.kwarg is not None:
        yield function.args.kwarg


def _contains_explicit_any(tree: ast.Module) -> bool:
    if any(
        (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        for node in ast.walk(tree)
    ):
        return True
    annotations: list[ast.expr | None] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations.append(node.returns)
            annotations.extend(parameter.annotation for parameter in _function_parameters(node))
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
    return any(
        isinstance(annotation, ast.Constant)
        and isinstance(annotation.value, str)
        and _ANY_ANNOTATION_RE.search(annotation.value)
        for annotation in annotations
    )


def _imports_any(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "Any" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_ledger_has_no_runtime_method_rebinding_or_not_implemented_placeholders() -> None:
    rebound_methods: list[str] = []
    placeholders: list[str] = []
    for path, tree in _ledger_modules().items():
        rebinding_visitor = _ModuleLevelRebindingVisitor()
        rebinding_visitor.visit(tree)
        rebound_methods.extend(f"{path.name}:{line}" for line in rebinding_visitor.violations)
        for class_node in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for statement in class_node.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    _is_not_implemented_placeholder(statement)
                ):
                    placeholders.append(f"{path.name}:{statement.lineno}:{statement.name}")

    assert not rebound_methods, (
        "DefaultContextAdmissionLedger methods must be supplied by inheritance, not "
        f"module-level rebinding: {rebound_methods}"
    )
    assert not placeholders, (
        f"Ledger class methods must not be NotImplementedError placeholders: {placeholders}"
    )


def test_ledger_receiver_functions_are_methods_and_store_failure_is_inherited() -> None:
    modules = _ledger_modules()
    top_level_receivers: list[str] = []
    for path, tree in modules.items():
        for statement in tree.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parameter = _first_positional_parameter(statement)
            if parameter is not None and parameter.arg == "self":
                top_level_receivers.append(f"{path.name}:{statement.lineno}:{statement.name}")

    assert not top_level_receivers, (
        f"Ledger functions with a self receiver must be class methods: {top_level_receivers}"
    )

    ancestry = _final_ledger_ancestry(modules)
    assert len(ancestry) > 1, "DefaultContextAdmissionLedger must inherit its implementation"
    store_failure_methods = [
        (path, statement)
        for path, class_node in ancestry
        for statement in class_node.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "_set_store_failure"
    ]
    assert len(store_failure_methods) == 1, (
        "DefaultContextAdmissionLedger's local base chain must define exactly one "
        f"_set_store_failure method; found {len(store_failure_methods)}"
    )
    path, store_failure = store_failure_methods[0]
    assert not {"staticmethod", "classmethod"} & _decorator_names(store_failure), (
        f"{path.name}:{store_failure.lineno}: _set_store_failure must be an instance method"
    )
    receiver = _first_positional_parameter(store_failure)
    assert receiver is not None and receiver.arg == "self", (
        f"{path.name}:{store_failure.lineno}: _set_store_failure must receive self"
    )


def test_ledger_function_signatures_have_no_untyped_or_suppressed_escape_hatches() -> None:
    missing_annotations: list[str] = []
    explicit_any: list[str] = []
    suppressions: list[str] = []
    for path, tree in _ledger_modules().items():
        for function, has_implicit_receiver in _function_contexts(tree):
            receiver = _first_positional_parameter(function) if has_implicit_receiver else None
            for parameter in _function_parameters(function):
                if parameter is not receiver and parameter.annotation is None:
                    missing_annotations.append(
                        f"{path.name}:{function.lineno}:{function.name} parameter {parameter.arg}"
                    )
            if function.returns is None:
                missing_annotations.append(f"{path.name}:{function.lineno}:{function.name} return")
        if _contains_explicit_any(tree) or _imports_any(tree):
            explicit_any.append(path.name)
        suppressions.extend(
            f"{path.name}:{line_number}"
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _TYPE_CHECKER_SUPPRESSION_RE.search(line)
        )

    assert not missing_annotations, (
        "Ledger functions need annotations for every non-receiver parameter and return: "
        + ", ".join(missing_annotations)
    )
    assert not explicit_any, f"Ledger source must not import or use explicit Any: {explicit_any}"
    assert not suppressions, (
        "Ledger source must not use type-checker suppression comments: " + ", ".join(suppressions)
    )
