"""Standalone test-path filtering logic. No pytest dependencies."""
from __future__ import annotations

import ast
import enum
import fnmatch
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class FilterMode(enum.StrEnum):
    NONE = "none"
    CONSERVATIVE = "conservative"
    AGGRESSIVE = "aggressive"


class ImportContext(enum.StrEnum):
    TOP_LEVEL = "top_level"
    CONDITIONAL = "conditional"
    TYPE_CHECKING = "type_checking"
    DEFERRED = "deferred"
    IMPORTLIB = "importlib"


BUCKET_A_PATTERNS: frozenset[str] = frozenset({
    "tests/conftest.py",
    "tests/_helpers.py",
    "tests/arch/_helpers.py",
    "tests/arch/_rules.py",
    "pyproject.toml",
    "uv.lock",
    ".pre-commit-config.yaml",
    "src/autoskillit/server/_factory.py",
})

BUCKET_A_GLOBS: tuple[str, ...] = ("tests/*/conftest.py",)

ALWAYS_RUN_CONSERVATIVE: frozenset[str] = frozenset({
    "arch", "contracts", "infra", "docs",
})

ALWAYS_RUN_AGGRESSIVE: frozenset[str] = frozenset({
    "arch", "contracts",
})

_LARGE_CHANGESET_THRESHOLD: int = 30

# ---------------------------------------------------------------------------
# Layer cascade maps
# ---------------------------------------------------------------------------

LAYER_CASCADE_CONSERVATIVE: dict[str, frozenset[str]] = {
    # L0 — imported by everything
    "core": frozenset({
        "core", "config", "execution", "pipeline", "workspace",
        "recipe", "migration", "server", "cli",
        "hooks", "skills",
    }),
    # L1
    "config": frozenset({
        "config", "pipeline", "workspace", "server", "cli",
    }),
    "execution": frozenset({
        "execution", "core", "workspace", "migration",
        "server", "cli", "infra", "skills",
    }),
    "pipeline": frozenset({
        "pipeline", "execution", "server", "infra",
    }),
    "workspace": frozenset({
        "workspace", "recipe", "server", "cli", "skills",
    }),
    # L2
    "recipe": frozenset({
        "recipe", "execution", "server", "cli", "infra", "skills",
    }),
    "migration": frozenset({
        "migration", "server",
    }),
    # L3
    "server": frozenset({
        "server", "cli", "infra",
    }),
    "cli": frozenset({
        "cli",
    }),
    # Infra (non-layered)
    "hooks": frozenset({
        "hooks", "infra", "cli",
    }),
    "hook_registry": frozenset({
        "server", "infra", "cli", "docs",
    }),
}

LAYER_CASCADE_AGGRESSIVE: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "config": frozenset({"config"}),
    "execution": frozenset({"execution"}),
    "pipeline": frozenset({"pipeline"}),
    "workspace": frozenset({"workspace"}),
    "recipe": frozenset({"recipe"}),
    "migration": frozenset({"migration"}),
    "server": frozenset({"server"}),
    "cli": frozenset({"cli"}),
    "hooks": frozenset({"hooks"}),
    "hook_registry": frozenset({"hooks"}),
}

# ---------------------------------------------------------------------------
# ASTImportWalker
# ---------------------------------------------------------------------------


class ASTImportWalker(ast.NodeVisitor):
    """Extract all import statements from a Python source file with context tracking."""

    def __init__(self) -> None:
        self.imports: list[tuple[str, ImportContext]] = []
        self._context_stack: list[ImportContext] = []

    @property
    def _current_context(self) -> ImportContext:
        return self._context_stack[-1] if self._context_stack else ImportContext.TOP_LEVEL

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name, self._current_context))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            module = ("." * (node.level or 0)) + node.module
        else:
            module = "." * (node.level or 0)
        self.imports.append((module, self._current_context))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._context_stack.append(ImportContext.DEFERRED)
        self.generic_visit(node)
        self._context_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._context_stack.append(ImportContext.DEFERRED)
        self.generic_visit(node)
        self._context_stack.pop()

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking_guard(node.test):
            self._context_stack.append(ImportContext.TYPE_CHECKING)
            for child in node.body:
                self.visit(child)
            self._context_stack.pop()
            for child in node.orelse:
                self.visit(child)
        else:
            self._context_stack.append(ImportContext.CONDITIONAL)
            self.generic_visit(node)
            self._context_stack.pop()

    def visit_Try(self, node: ast.Try) -> None:
        self._context_stack.append(ImportContext.CONDITIONAL)
        self.generic_visit(node)
        self._context_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_importlib_call(node) and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                self.imports.append((arg.value, ImportContext.IMPORTLIB))
        self.generic_visit(node)

    @staticmethod
    def _is_type_checking_guard(test: ast.expr) -> bool:
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            return True
        if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            return True
        return False

    @staticmethod
    def _is_importlib_call(node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            return True
        return False


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def git_changed_files(
    cwd: str | Path,
    base_ref: str | None = None,
) -> set[str] | None:
    """Return set of changed files relative to base_ref, or None on failure."""
    import os

    if base_ref is None:
        base_ref = os.environ.get(
            "AUTOSKILLIT_TEST_BASE_REF",
            os.environ.get("GITHUB_BASE_REF"),
        )
    if base_ref is None:
        return None

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None

    lines = result.stdout.strip().splitlines()
    return {line.strip() for line in lines if line.strip()}


def check_bucket_a(changed_files: set[str]) -> bool:
    """Return True if any changed file triggers a full test run."""
    for f in changed_files:
        if f in BUCKET_A_PATTERNS:
            return True
        for glob_pat in BUCKET_A_GLOBS:
            if fnmatch.fnmatch(f, glob_pat):
                return True
    return False


def load_manifest(path: str | Path) -> dict[str, Any] | None:
    """Load .autoskillit/test-filter-manifest.yaml, or None if absent."""
    import yaml

    manifest_path = Path(path) / ".autoskillit" / "test-filter-manifest.yaml"
    if not manifest_path.exists():
        return None
    with manifest_path.open() as f:
        return yaml.safe_load(f)


def apply_manifest(
    changed_files: set[str],
    manifest: dict[str, Any] | None,
) -> set[str]:
    """Return test directories matched by manifest patterns for the changed files."""
    if manifest is None:
        return set()
    result: set[str] = set()
    patterns = manifest.get("patterns", {})
    for pattern, test_dirs in patterns.items():
        for f in changed_files:
            if fnmatch.fnmatch(f, pattern):
                if isinstance(test_dirs, list):
                    result.update(test_dirs)
                elif isinstance(test_dirs, str):
                    result.add(test_dirs)
                break
    return result


# ---------------------------------------------------------------------------
# Re-export closure
# ---------------------------------------------------------------------------


def _expand_reexport_closure(
    changed_src_files: set[str],
    src_root: str | Path,
) -> set[str]:
    """Expand changed files to include __init__.py files that directly re-export from them.

    Walks up parent directories checking each __init__.py via AST parsing.
    Only handles direct re-exports (from .module import X); transitive
    chains through intermediate hubs are covered by cascade maps.
    """
    src_root = Path(src_root)
    expanded = set(changed_src_files)

    for changed in list(changed_src_files):
        changed_path = src_root / changed
        if not changed_path.exists():
            continue
        module_name = changed_path.stem
        parent = changed_path.parent
        while parent >= src_root:
            init_path = parent / "__init__.py"
            if init_path.exists():
                try:
                    source = init_path.read_text(errors="replace")
                    tree = ast.parse(source, filename=str(init_path))
                    walker = ASTImportWalker()
                    walker.visit(tree)
                    for mod, _ctx in walker.imports:
                        bare = mod.lstrip(".")
                        if bare == module_name or bare.endswith(f".{module_name}"):
                            rel_init = str(init_path.relative_to(src_root))
                            expanded.add(rel_init)
                            break
                except SyntaxError:
                    pass
            parent = parent.parent

    return expanded


# ---------------------------------------------------------------------------
# build_test_scope
# ---------------------------------------------------------------------------


def _file_to_package(filepath: str) -> str | None:
    """Extract the autoskillit subpackage name from a source file path.

    e.g. 'src/autoskillit/core/io.py' -> 'core'
         'src/autoskillit/execution/headless.py' -> 'execution'
         'src/autoskillit/server/_factory.py' -> 'server'
         'src/autoskillit/hook_registry.py' -> 'hook_registry'
    """
    parts = Path(filepath).parts
    try:
        idx = parts.index("autoskillit")
        if idx + 1 < len(parts):
            candidate = parts[idx + 1]
            if candidate.endswith(".py"):
                return candidate.removesuffix(".py")
            return candidate
    except ValueError:
        pass
    return None


def build_test_scope(
    changed_files: set[str] | None,
    mode: FilterMode,
    manifest: dict[str, Any] | None = None,
    tests_root: str | Path = "tests",
    src_root: str | Path = "src/autoskillit",
) -> set[Path] | None:
    """Compute the set of test paths to run, or None for a full run.

    Algorithm:
    1. None changed_files -> None (fail-open)
    2. >30 files -> None (large changeset)
    3. Bucket A triggered -> None (full run)
    4. Classify: src Python -> cascade, test Python -> direct, non-Python -> manifest
    5. Compute always-run set for mode (includes arch/contracts for both modes)
    6. Union all sets
    7. For aggressive mode -> AST-based file-level refinement (future)
    8. Resolve to concrete paths
    """
    if mode == FilterMode.NONE:
        return None

    if changed_files is None:
        return None

    if len(changed_files) > _LARGE_CHANGESET_THRESHOLD:
        return None

    if check_bucket_a(changed_files):
        return None

    tests_root = Path(tests_root)

    cascade_map = (
        LAYER_CASCADE_CONSERVATIVE
        if mode == FilterMode.CONSERVATIVE
        else LAYER_CASCADE_AGGRESSIVE
    )
    always_run = (
        ALWAYS_RUN_CONSERVATIVE
        if mode == FilterMode.CONSERVATIVE
        else ALWAYS_RUN_AGGRESSIVE
    )

    test_dirs: set[str] = set()
    direct_test_files: set[str] = set()
    for f in changed_files:
        if f.startswith("tests/") and f.endswith(".py"):
            direct_test_files.add(f)
        elif f.startswith("src/") and f.endswith(".py"):
            pkg = _file_to_package(f)
            if pkg and pkg in cascade_map:
                test_dirs.update(cascade_map[pkg])
            else:
                return None
        elif f.endswith(".py"):
            return None
        else:
            manifest_dirs = apply_manifest({f}, manifest)
            if manifest_dirs:
                test_dirs.update(manifest_dirs)

    test_dirs.update(always_run)

    result: set[Path] = set()
    for d in test_dirs:
        dir_path = tests_root / d
        if dir_path.is_dir():
            result.add(dir_path)

    for f in direct_test_files:
        result.add(Path(f))

    return result
