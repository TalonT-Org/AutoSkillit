"""Python AST collector: walks Python source via stdlib ``ast`` and emits typed evidence records.

Decomposed from the original ``collectors/extractors.py`` per #4836.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from autoskillit.core import (
    CollectorReport,
    CollectorStatus,
    EvidenceRecord,
    NodeKey,
)

from ...graph import SubjectNamespace
from .._bounded import (
    CollectorLimits,
    CollectorSafetyError,
    read_contained_file,
)
from ._evidence import _evidence, _report
from ._file_search import _scoped_paths

__all__ = [
    "collect_python_ast",
    "_qualified_name",
    "_is_named_base",
]


def _qualified_name(node: ast.expr) -> str | None:
    """Return the static spelling of a simple name or attribute access."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def _is_named_base(node: ast.expr, names: frozenset[str]) -> bool:
    qualified = _qualified_name(node)
    return qualified is not None and qualified.rsplit(".", maxsplit=1)[-1] in names


def collect_python_ast(
    root: Path, snapshot_digest: str, scope: str, limits: CollectorLimits
) -> CollectorReport:
    collector_id = "python-ast"
    evidence: list[EvidenceRecord] = []

    def truncated_report() -> CollectorReport:
        return _report(
            collector_id,
            snapshot_digest,
            CollectorStatus.TRUNCATED,
            ("symbol limit exceeded",),
            tuple(evidence),
        )

    def observe(
        subject: NodeKey,
        path: str,
        line: int,
        claim: str,
        *,
        unknowns: tuple[str, ...] = (),
    ) -> bool:
        evidence.append(
            replace(
                _evidence(collector_id, snapshot_digest, path, line, claim),
                subject=subject,
                unknowns=unknowns,
            )
        )
        return len(evidence) >= limits.max_matches

    try:
        paths = tuple(path for path in _scoped_paths(root, scope, limits) if path.endswith(".py"))
        for path in paths:
            source = read_contained_file(root, path, limits).decode("utf-8", "replace")
            tree = ast.parse(source, filename=path, type_comments=True)
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    namespace = SubjectNamespace.PYTHON_SYMBOL
                    if isinstance(node, ast.ClassDef):
                        if any(
                            _is_named_base(base, frozenset({"Protocol"})) for base in node.bases
                        ):
                            namespace = SubjectNamespace.PYTHON_PROTOCOL
                        elif any(
                            _is_named_base(base, frozenset({"ABC", "ABCMeta"}))
                            for base in node.bases
                        ):
                            namespace = SubjectNamespace.PYTHON_NOMINAL_PROTOCOL
                    if observe(
                        NodeKey(namespace.value, f"{path}:{node.lineno}:{node.name}"),
                        path,
                        node.lineno,
                        node.name,
                    ):
                        return truncated_report()
                    for decorator in node.decorator_list:
                        decorated = (
                            decorator.func if isinstance(decorator, ast.Call) else decorator
                        )
                        decorator_name = _qualified_name(decorated)
                        if (
                            decorator_name is not None
                            and decorator_name.rsplit(".", maxsplit=1)[-1] in {"override", "patch"}
                            and observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_RUNTIME_PATCH.value,
                                    f"{path}:{node.lineno}:{decorator_name}",
                                ),
                                path,
                                node.lineno,
                                f"decorator {decorator_name}",
                            )
                        ):
                            return truncated_report()
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if observe(
                            NodeKey(SubjectNamespace.PYTHON_IMPORT.value, alias.name),
                            path,
                            node.lineno,
                            f"import {alias.name}",
                        ):
                            return truncated_report()
                elif isinstance(node, ast.ImportFrom):
                    module = f"{'.' * node.level}{node.module or ''}"
                    namespace = (
                        SubjectNamespace.PYTHON_REEXPORT
                        if path.endswith("__init__.py")
                        else SubjectNamespace.PYTHON_IMPORT
                    )
                    for alias in node.names:
                        if observe(
                            NodeKey(namespace.value, f"{module}:{alias.name}"),
                            path,
                            node.lineno,
                            f"from {module} import {alias.name}",
                        ):
                            return truncated_report()
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(
                            node.value, (ast.Name, ast.Attribute)
                        ):
                            if observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_ALIAS.value,
                                    f"{path}:{target.id}",
                                ),
                                path,
                                node.lineno,
                                f"alias {target.id}",
                            ):
                                return truncated_report()
                        if isinstance(target, ast.Name) and "registry" in target.id.lower():
                            if observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_REGISTRY.value,
                                    f"{path}:{node.lineno}:{target.id}",
                                ),
                                path,
                                node.lineno,
                                f"registry {target.id}",
                            ):
                                return truncated_report()
                        elif isinstance(target, ast.Attribute):
                            target_name = _qualified_name(target)
                            if target_name is not None and observe(
                                NodeKey(
                                    SubjectNamespace.PYTHON_RUNTIME_WIRING.value,
                                    f"{path}:{node.lineno}:{target_name}",
                                ),
                                path,
                                node.lineno,
                                f"wiring {target_name}",
                            ):
                                return truncated_report()
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_DECLARATION.value,
                            f"{path}:{node.lineno}:{node.target.id}",
                        ),
                        path,
                        node.lineno,
                        f"declaration {node.target.id}",
                    ):
                        return truncated_report()
                elif isinstance(node, ast.Call):
                    call_name = _qualified_name(node.func)
                    if call_name is None:
                        continue
                    if observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_CALL.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"call {call_name}",
                    ):
                        return truncated_report()
                    terminal_name = call_name.rsplit(".", maxsplit=1)[-1]
                    if terminal_name in {"import_module", "__import__"}:
                        import_name = (
                            node.args[0].value
                            if node.args
                            and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)
                            else "<unresolved>"
                        )
                        if observe(
                            NodeKey(SubjectNamespace.PYTHON_DYNAMIC_IMPORT.value, import_name),
                            path,
                            node.lineno,
                            f"dynamic import {import_name}",
                            unknowns=("dynamic import target is not statically resolved",)
                            if import_name == "<unresolved>"
                            else (),
                        ):
                            return truncated_report()
                    if terminal_name in {"register", "setattr", "wire"} and observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_RUNTIME_WIRING.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"runtime wiring {call_name}",
                    ):
                        return truncated_report()
                    if terminal_name in {"override", "patch", "setattr"} and observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_RUNTIME_PATCH.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"runtime patch {call_name}",
                    ):
                        return truncated_report()
                    if (
                        path.startswith("tests/") or Path(path).name.startswith("test_")
                    ) and observe(
                        NodeKey(
                            SubjectNamespace.PYTHON_TEST_CONSUMER.value,
                            f"{path}:{node.lineno}:{call_name}",
                        ),
                        path,
                        node.lineno,
                        f"test consumer {call_name}",
                    ):
                        return truncated_report()
    except (CollectorSafetyError, SyntaxError) as exc:
        return _report(
            collector_id,
            snapshot_digest,
            CollectorStatus.FAILED,
            (str(exc),),
            tuple(evidence),
        )
    return _report(
        collector_id, snapshot_digest, CollectorStatus.SUCCEEDED, evidence=tuple(evidence)
    )
