#!/usr/bin/env python3
"""Guard launch-facing retirement operations against partial-result regressions.

This is deliberately an intraprocedural guard. It checks exceptions originated by
registered function bodies and syntactically discarded calls to registered total-
return operations; it does not attempt call-graph analysis. Exceptions raised by a
callee remain the responsibility of the launch-path corruption matrix tests, which
exercise the real call graph against unsafe persisted inputs.

Exit 0 when every registered function exists, originates only allowlisted
exceptions, and has no call result discarded as a bare expression. Exit 1 with a
description of every violation otherwise.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

LAUNCH_TOTAL_FUNCTIONS: Mapping[tuple[str, str], frozenset[str]] = {
    (
        "autoskillit/core/runtime/session_registry.py",
        "bind_session_owner",
    ): frozenset({"ValueError"}),
    ("autoskillit/core/_retiring_cache.py", "append_retiring_record"): frozenset(),
    ("autoskillit/core/_retiring_cache.py", "remove_retiring_records"): frozenset(),
    ("autoskillit/core/_retiring_cache.py", "due_retiring_records"): frozenset({"ValueError"}),
    ("autoskillit/core/_active_kitchens.py", "register_active_kitchen"): frozenset(),
    ("autoskillit/core/_active_kitchens.py", "unregister_active_kitchen"): frozenset(),
    (
        "autoskillit/core/_plugin_artifact_retirement.py",
        "PluginArtifactRetirementEngine.cancel_obsolete_retirements",
    ): frozenset(),
    (
        "autoskillit/core/_plugin_artifact_retirement.py",
        "PluginArtifactRetirementEngine.enqueue_retirement",
    ): frozenset({"PluginArtifactValidationError"}),
    (
        "autoskillit/workspace/_projection_cache.py",
        "ProjectedPluginRetirementOwner.cancel_obsolete_retirements",
    ): frozenset(),
    (
        "autoskillit/workspace/_projection_cache.py",
        "prune_stale_projections",
    ): frozenset(),
}

MUST_CONSUME_TOTAL_RESULTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("autoskillit/core/runtime/session_registry.py", "bind_session_owner"),
        ("autoskillit/core/_retiring_cache.py", "append_retiring_record"),
        ("autoskillit/core/_retiring_cache.py", "remove_retiring_records"),
        ("autoskillit/core/_retiring_cache.py", "due_retiring_records"),
        ("autoskillit/core/_active_kitchens.py", "register_active_kitchen"),
        ("autoskillit/core/_active_kitchens.py", "unregister_active_kitchen"),
        (
            "autoskillit/core/_plugin_artifact_retirement.py",
            "PluginArtifactRetirementEngine.enqueue_retirement",
        ),
        (
            "autoskillit/core/_plugin_artifact_retirement.py",
            "PluginArtifactRetirementEngine.cancel_obsolete_retirements",
        ),
        (
            "autoskillit/workspace/_projection_cache.py",
            "ProjectedPluginRetirementOwner.enqueue_retirement",
        ),
        (
            "autoskillit/workspace/_projection_cache.py",
            "ProjectedPluginRetirementOwner.cancel_obsolete_retirements",
        ),
        (
            "autoskillit/cli/install/_plugin_artifact.py",
            "InstalledPluginArtifactRetirementOwner.enqueue_retirement",
        ),
        (
            "autoskillit/cli/install/_plugin_artifact.py",
            "InstalledPluginArtifactRetirementOwner.cancel_obsolete_retirements",
        ),
        (
            "autoskillit/workspace/_projected_artifact/_generation_publication.py",
            "GenerationArtifactRetirementOwner.enqueue_retirement",
        ),
        (
            "autoskillit/workspace/_projected_artifact/_generation_publication.py",
            "GenerationArtifactRetirementOwner.cancel_obsolete_retirements",
        ),
    }
)


def _module_to_relpath(module: str) -> str:
    return module.replace(".", "/") + ".py"


class _ImportMap:
    """Resolve imported function aliases and module-qualified calls."""

    def __init__(self, tree: ast.Module) -> None:
        self._from_imports: dict[str, tuple[str, str]] = {}
        self._module_imports: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                for alias in node.names:
                    self._from_imports[alias.asname or alias.name] = (
                        _module_to_relpath(node.module),
                        alias.name,
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    self._module_imports[local] = alias.name

    def resolve_call(self, call: ast.Call, current_module: str) -> tuple[str, str] | None:
        func = call.func
        if isinstance(func, ast.Name):
            return self._from_imports.get(func.id, (current_module, func.id))
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = self._module_imports.get(func.value.id)
            if module is not None:
                return (_module_to_relpath(module), func.attr)
        return None


_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _functions_by_qualname(tree: ast.Module) -> dict[str, _FunctionNode]:
    functions: dict[str, _FunctionNode] = {}

    def visit_body(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit_body(node.body, (*prefix, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[".".join((*prefix, node.name))] = node

    visit_body(tree.body)
    return functions


def _parse_registered_modules(
    src_root: Path,
) -> tuple[dict[str, ast.Module], list[str]]:
    modules = {module for module, _ in LAUNCH_TOTAL_FUNCTIONS}
    modules.update(module for module, _ in MUST_CONSUME_TOTAL_RESULTS)
    parsed: dict[str, ast.Module] = {}
    violations: list[str] = []
    for module in sorted(modules):
        path = src_root / module
        if not path.is_file():
            violations.append(f"registered module does not exist: {module}")
            continue
        parsed[module] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return parsed, violations


def find_missing_registered_functions(src_root: Path = SRC_ROOT) -> list[str]:
    """Report registry entries that do not resolve to live functions."""
    parsed, violations = _parse_registered_modules(src_root)
    registered = set(LAUNCH_TOTAL_FUNCTIONS) | set(MUST_CONSUME_TOTAL_RESULTS)
    functions_by_module = {module: _functions_by_qualname(tree) for module, tree in parsed.items()}
    for module, qualname in sorted(registered):
        if module in parsed and qualname not in functions_by_module[module]:
            violations.append(f"registered function does not exist: {module}:{qualname}")
    return violations


class _OriginatedRaiseVisitor(ast.NodeVisitor):
    def __init__(self, allowed: frozenset[str], module: str, qualname: str) -> None:
        self.allowed = allowed
        self.module = module
        self.qualname = qualname
        self.violations: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is None:
            return
        exception_name: str | None = None
        if isinstance(node.exc, ast.Name):
            exception_name = node.exc.id
        elif isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
            exception_name = node.exc.func.id
        if exception_name is None:
            detail = "unresolvable exception expression"
        elif exception_name not in self.allowed:
            detail = f"disallowed exception {exception_name}"
        else:
            return
        self.violations.append(f"{self.module}:{node.lineno}: {self.qualname} originates {detail}")


def find_originated_exception_violations(src_root: Path = SRC_ROOT) -> list[str]:
    """Report disallowed raises written directly in registered function bodies."""
    parsed, _ = _parse_registered_modules(src_root)
    violations: list[str] = []
    for (module, qualname), allowed in LAUNCH_TOTAL_FUNCTIONS.items():
        tree = parsed.get(module)
        if tree is None:
            continue
        function = _functions_by_qualname(tree).get(qualname)
        if function is None:
            continue
        visitor = _OriginatedRaiseVisitor(allowed, module, qualname)
        for statement in function.body:
            visitor.visit(statement)
        violations.extend(visitor.violations)
    return violations


def find_discarded_total_result_violations(src_root: Path = SRC_ROOT) -> list[str]:
    """Report registered total-return calls used as bare expression statements."""
    direct_targets = {
        (module, qualname)
        for module, qualname in MUST_CONSUME_TOTAL_RESULTS
        if "." not in qualname
    }
    direct_names = {qualname for _, qualname in direct_targets}
    method_names = {
        qualname.rsplit(".", 1)[-1]
        for _, qualname in MUST_CONSUME_TOTAL_RESULTS
        if "." in qualname
    }
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        module = path.relative_to(src_root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            continue
        tree = ast.parse(source, filename=str(path))
        imports = _ImportMap(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            target = imports.resolve_call(call, module)
            method_name = call.func.attr if isinstance(call.func, ast.Attribute) else None
            direct_name = target[1] if target is not None else None
            if direct_name not in direct_names and method_name not in method_names:
                continue
            rendered_target = (
                f"{target[0]}:{target[1]}"
                if direct_name in direct_names and target is not None
                else f"*.{method_name}"
            )
            violations.append(
                f"{module}:{node.lineno}: discarded total result from {rendered_target}"
            )
    return violations


def check(src_root: Path = SRC_ROOT) -> list[str]:
    """Return every launch-path totality violation under *src_root*."""
    return [
        *find_missing_registered_functions(src_root),
        *find_originated_exception_violations(src_root),
        *find_discarded_total_result_violations(src_root),
    ]


def main() -> int:
    violations = check()
    if violations:
        print("Launch-path totality violations found:\n")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("Every registered launch-path function is total and every result is consumed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
