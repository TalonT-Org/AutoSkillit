"""Runtime import closure of the Python an installed plugin tree ships.

Shared by tests/arch/test_hooks_are_stdlib_only.py and
tests/contracts/test_projection_hook_relocatability.py. Discovery derives from
iter_public_plugin_asset_files, the same predicate the installed-tree copier uses.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from autoskillit.workspace._installed._projection_assets import iter_public_plugin_asset_files

_NAMED_IMPORT_CALLS = frozenset({"import_module", "__import__", "run_module"})
_OPAQUE_LOAD_CALLS = frozenset(
    {
        "spec_from_file_location",
        "spec_from_loader",
        "module_from_spec",
        "exec_module",
        "SourceFileLoader",
        "load_module",
        "run_path",
        "exec",
        "eval",
    }
)
_MODULE_QUALNAME = "<module>"


@dataclass(frozen=True, slots=True)
class ImportRef:
    module: str
    level: int
    lineno: int


@dataclass(frozen=True, slots=True)
class RuntimeImports:
    refs: tuple[ImportRef, ...]
    dynamic_sites: frozenset[str]


@dataclass(frozen=True, slots=True)
class ImportClosureReport:
    violations: tuple[str, ...]
    root_dependencies: Mapping[str, frozenset[str]]
    dynamic_sites: frozenset[tuple[str, str]]


# Shared by every shipped-tree caller so facade exclusions cannot diverge.
PACKAGE_MODE_FACADES: Mapping[str, str] = MappingProxyType(
    {
        "hooks/__init__.py": (
            "Package-mode facade: runs only when `autoskillit.hooks` is imported inside the "
            "venv. Installed-tree hooks run as scripts via hooks/_dispatch.py and never import "
            "the `hooks` package (test_package_mode_facades_are_unreachable_from_shipped_code)."
        ),
    }
)


def _runtime_nodes(node: ast.AST, qualname: str) -> Iterator[tuple[ast.AST, str]]:
    if isinstance(node, ast.If):
        test = node.test
        if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        ):
            for statement in node.orelse:
                yield from _runtime_nodes(statement, qualname)
            return
    yield node, qualname
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        body_qualname = node.name if qualname == _MODULE_QUALNAME else f"{qualname}.{node.name}"
        for child in ast.iter_child_nodes(node):
            # Defaults, decorators, and bases execute before entering the new scope.
            yield from _runtime_nodes(child, body_qualname if child in node.body else qualname)
    else:
        for child in ast.iter_child_nodes(node):
            yield from _runtime_nodes(child, qualname)


def runtime_imports(tree: ast.Module) -> RuntimeImports:
    """Collect runtime imports and unresolved loader calls with their enclosing scope."""
    refs: list[ImportRef] = []
    dynamic_sites: set[str] = set()
    for node, qualname in _runtime_nodes(tree, _MODULE_QUALNAME):
        if isinstance(node, ast.Import):
            refs.extend(ImportRef(alias.name, 0, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            refs.append(ImportRef(node.module or "", node.level, node.lineno))
        elif isinstance(node, ast.Call):
            loader = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if loader in _NAMED_IMPORT_CALLS:
                if (
                    len(node.args) == 1
                    and not node.keywords
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and not node.args[0].value.startswith(".")
                ):
                    refs.append(ImportRef(node.args[0].value, 0, node.lineno))
                else:
                    dynamic_sites.add(qualname)
            elif loader in _OPAQUE_LOAD_CALLS:
                dynamic_sites.add(qualname)

    return RuntimeImports(tuple(refs), frozenset(dynamic_sites))


def shipped_python_files(source_root: Path) -> tuple[Path, ...]:
    """Return exactly the Python files admitted by the installed-tree copier."""
    return tuple(p for p in iter_public_plugin_asset_files(source_root) if p.suffix == ".py")


def _resolve_import(
    ref: ImportRef, path: Path, source_root: Path, search_dirs: tuple[Path, ...]
) -> tuple[str | None, str | None]:
    """Return the package-root dependency or the closure violation for one import."""
    rel = path.relative_to(source_root).as_posix()
    if ref.level:
        base = path.parent
        for _ in range(ref.level - 1):
            base = base.parent
        if not base.is_relative_to(source_root / "hooks"):
            return None, (
                f"{rel}:{ref.lineno}: relative import {'.' * ref.level}{ref.module} escapes hooks/"
            )
        target = base.joinpath(*ref.module.split("."))
        if not target.with_suffix(".py").is_file() and not target.is_dir():
            return None, (
                f"{rel}:{ref.lineno}: relative import {'.' * ref.level}{ref.module} "
                "resolves to no file an installed plugin tree ships"
            )
        return None, None
    if not ref.module:
        return None, None
    top = ref.module.partition(".")[0]
    if top == "__future__" or top in sys.stdlib_module_names:
        return None, None
    if (source_root / f"{top}.py").is_file():
        return f"{top}.py", None
    if (source_root / top).is_dir():
        return top, None
    if any(
        (directory / f"{top}.py").is_file() or (directory / top).is_dir()
        for directory in search_dirs
    ):
        return None, None
    return None, (
        f"{rel}:{ref.lineno}: {ref.module!r} resolves to no stdlib module and no "
        "file an installed plugin tree ships (third-party, autoskillit.*, or missing)"
    )


def scan_import_closure(
    source_root: Path, files: Iterable[Path], *, skip: Collection[str] = ()
) -> ImportClosureReport:
    """Resolve imports against stdlib, package-root entries, and the shipped hooks tree."""
    source_root = source_root.resolve()
    hooks_root = source_root / "hooks"
    search_dirs = (
        hooks_root,
        *(p for p in hooks_root.rglob("*") if p.is_dir() and "__pycache__" not in p.parts),
    )
    violations: list[str] = []
    root_dependencies: dict[str, set[str]] = {}
    dynamic_sites: set[tuple[str, str]] = set()

    for file in files:
        path = file.resolve()
        rel = path.relative_to(source_root).as_posix()
        if rel in skip:
            continue
        imports = runtime_imports(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        dynamic_sites.update((rel, qualname) for qualname in imports.dynamic_sites)
        for ref in imports.refs:
            dependency, violation = _resolve_import(ref, path, source_root, search_dirs)
            if dependency is not None:
                root_dependencies.setdefault(dependency, set()).add(rel)
            if violation is not None:
                violations.append(violation)

    return ImportClosureReport(
        violations=tuple(sorted(violations)),
        root_dependencies=MappingProxyType(
            {name: frozenset(importers) for name, importers in sorted(root_dependencies.items())}
        ),
        dynamic_sites=frozenset(dynamic_sites),
    )


def scan_shipped_import_closure(source_root: Path) -> ImportClosureReport:
    """Scan the shipped set with the same package-facade exclusions for every caller."""
    return scan_import_closure(
        source_root, shipped_python_files(source_root), skip=PACKAGE_MODE_FACADES.keys()
    )
