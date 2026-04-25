"""Standalone test-path filtering logic. No pytest dependencies."""

from __future__ import annotations

import ast
import datetime
import enum
import fnmatch
import json
import logging
import re
import subprocess
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import pathspec

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


BUCKET_A_PATTERNS: frozenset[str] = frozenset(
    {
        "tests/conftest.py",
        "tests/_helpers.py",
        "tests/arch/_helpers.py",
        "tests/arch/_rules.py",
        "pyproject.toml",
        "uv.lock",
        ".pre-commit-config.yaml",
        "src/autoskillit/server/_factory.py",
    }
)

BUCKET_A_GLOBS: tuple[str, ...] = ("tests/*/conftest.py",)

# Matches lines that only change a version string: -version = "0.9.x" / +version = "0.9.y"
_VERSION_LINE_RE: re.Pattern[str] = re.compile(r'^[+-]version\s*=\s*"[^"]*"', re.IGNORECASE)

ALWAYS_RUN_CONSERVATIVE: frozenset[str] = frozenset(
    {
        "arch",
        "contracts",
        "infra",
        "docs",
    }
)

ALWAYS_RUN_AGGRESSIVE: frozenset[str] = frozenset(
    {
        "arch",
        "contracts",
    }
)

# ---------------------------------------------------------------------------
# Tiered always-run configuration (conservative mode)
# ---------------------------------------------------------------------------

# Structural infra files that run unconditionally regardless of trigger conditions.
# These enforce cross-cutting registration, executability, and schema contracts.
_INFRA_UNCONDITIONAL_FILES: frozenset[str] = frozenset(
    {
        "test_hook_executability.py",
        "test_hook_registration_coverage.py",
        "test_manifest_completeness.py",
        "test_hook_registry.py",
        "test_guard_coverage.py",
        "test_session_scope_enforcement.py",
        "test_filter_activation.py",
        "test_schema_version_convention.py",
        "test_release_sanity.py",
    }
)

# Conditions that trigger inclusion of the full infra/ directory.
_INFRA_HOOK_TRIGGER_PREFIX: str = "src/autoskillit/hooks/"
_INFRA_CI_TRIGGER_PREFIX: str = ".github/"
_INFRA_CI_TRIGGER_FILES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        ".pre-commit-config.yaml",
        "Taskfile.yml",
    }
)

# Conditions that trigger inclusion of the docs/ directory.
_DOCS_TRIGGER_PREFIX: str = "docs/"
_DOCS_TRIGGER_FILES: frozenset[str] = frozenset({"README.md", "CLAUDE.md"})

# Tier-1 unconditional always-run for conservative mode: arch+contracts only.
# Decoupled from ALWAYS_RUN_AGGRESSIVE so future additions to that constant
# cannot silently alter conservative behavior.
_ALWAYS_RUN_CONSERVATIVE_UNCONDITIONAL: frozenset[str] = frozenset({"arch", "contracts"})

_LARGE_CHANGESET_THRESHOLD: int = 30

# ---------------------------------------------------------------------------
# core/ module-level cascade classification
# ---------------------------------------------------------------------------

_CORE_UNIVERSAL_MODULES: frozenset[str] = frozenset(
    {
        "io",
        "logging",
        "types",
        "_type_constants",
        "_type_protocols",
        "_type_enums",
        "_type_subprocess",
        "_type_results",
        "_type_resume",
        "_type_helpers",
    }
)

MODULE_CASCADE_CORE: dict[str, frozenset[str]] = {
    "readiness": frozenset({"core", "server"}),
    "feature_flags": frozenset({"core", "cli", "config", "server", "workspace"}),
    "kitchen_state": frozenset({"core", "cli"}),
    "branch_guard": frozenset({"core", "pipeline", "server", "workspace"}),
    "_plugin_ids": frozenset({"core", "cli", "server"}),
    "_terminal_table": frozenset({"core", "cli", "pipeline", "recipe"}),
    "_linux_proc": frozenset({"core", "cli", "execution", "fleet"}),
    "_plugin_cache": frozenset({"core", "cli", "server"}),
    "github_url": frozenset({"core", "cli", "execution", "server"}),
    "paths": frozenset(
        {
            "core",
            "cli",
            "config",
            "execution",
            "fleet",
            "migration",
            "recipe",
            "server",
            "workspace",
        }
    ),
    "_claude_env": frozenset({"core", "execution"}),
    "_version_snapshot": frozenset({"core", "execution"}),
    "claude_conventions": frozenset({"core", "server", "workspace"}),
}

# ---------------------------------------------------------------------------
# Layer cascade maps
# ---------------------------------------------------------------------------

LAYER_CASCADE_CONSERVATIVE: dict[str, frozenset[str]] = {
    # L0 — imported by everything
    "core": frozenset(
        {
            "core",
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "fleet",
            "server",
            "cli",
            "hooks",
            "skills",
        }
    ),
    # L1
    "config": frozenset(
        {
            "config",
            "pipeline",
            "workspace",
            "server",
            "cli",
        }
    ),
    "execution": frozenset(
        {
            "execution",
            "core",
            "workspace",
            "migration",
            "server",
            "cli",
            "infra",
            "skills",
        }
    ),
    "pipeline": frozenset(
        {
            "pipeline",
            "execution",
            "server",
            "infra",
        }
    ),
    "workspace": frozenset(
        {
            "workspace",
            "recipe",
            "fleet",
            "server",
            "cli",
            "skills",
        }
    ),
    # L2
    "recipe": frozenset(
        {
            "recipe",
            "execution",
            "server",
            "cli",
            "infra",
            "skills",
        }
    ),
    "migration": frozenset(
        {
            "migration",
            "server",
        }
    ),
    "fleet": frozenset(
        {
            "fleet",
            "server",
            "cli",
        }
    ),
    # L3
    "server": frozenset(
        {
            "server",
            "cli",
        }
    ),
    "cli": frozenset(
        {
            "cli",
        }
    ),
    # Infra (non-layered)
    "hooks": frozenset(
        {
            "hooks",
            "infra",
            "cli",
        }
    ),
    "hook_registry": frozenset(
        {
            "hooks",
            "server",
            "infra",
            "cli",
            "docs",
        }
    ),
    # Standalone modules (not subpackage directories)
    "planner": frozenset({"planner"}),
    "_llm_triage": frozenset({"test_llm_triage.py", "execution", "server", "recipe"}),
    "smoke_utils": frozenset({"test_smoke_utils.py", "recipe"}),
    "version": frozenset({"test_version.py", "server"}),
}

LAYER_CASCADE_AGGRESSIVE: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "config": frozenset({"config"}),
    "execution": frozenset({"execution"}),
    "pipeline": frozenset({"pipeline"}),
    "workspace": frozenset({"workspace"}),
    "recipe": frozenset({"recipe"}),
    "migration": frozenset({"migration"}),
    "fleet": frozenset({"fleet"}),
    "server": frozenset({"server"}),
    "cli": frozenset({"cli"}),
    "hooks": frozenset({"hooks"}),
    "hook_registry": frozenset({"hooks"}),
    "planner": frozenset({"planner"}),
    "_llm_triage": frozenset({"test_llm_triage.py"}),
    "smoke_utils": frozenset({"test_smoke_utils.py"}),
    "version": frozenset({"test_version.py"}),
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
            self._context_stack.append(ImportContext.CONDITIONAL)
            for child in node.orelse:
                self.visit(child)
            self._context_stack.pop()
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
        merge_base_result = subprocess.run(
            ["git", "merge-base", "HEAD", base_ref],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        merge_base_sha = merge_base_result.stdout.strip()
        if not merge_base_sha:
            warnings.warn("git merge-base returned empty output", stacklevel=2)
            return None

        diff_result = subprocess.run(
            ["git", "diff", "--name-only", merge_base_sha],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        warnings.warn(f"git diff failed (exit {exc.returncode}): {exc.stderr or ''}", stacklevel=2)
        return None
    except subprocess.TimeoutExpired:
        warnings.warn("git diff timed out after 10s", stacklevel=2)
        return None
    except FileNotFoundError:
        warnings.warn("git binary not found on PATH", stacklevel=2)
        return None

    files: set[str] = set()
    for line in diff_result.stdout.strip().splitlines():
        if line.strip():
            files.add(line.strip())

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if untracked.returncode == 0:
        for line in untracked.stdout.strip().splitlines():
            if line.strip():
                files.add(line.strip())

    return files


def check_bucket_a(changed_files: set[str]) -> bool:
    """Return True if any changed file triggers a full test run."""
    for f in changed_files:
        if f in BUCKET_A_PATTERNS:
            return True
        for glob_pat in BUCKET_A_GLOBS:
            if fnmatch.fnmatch(f, glob_pat):
                return True
    return False


def _is_only_version_changes_in_diff(
    cwd: str | Path,
    base_ref: str,
    *paths: str,
) -> bool:
    """Return True if every added/removed line in the diff of *paths* is a version string.

    Runs ``git merge-base HEAD base_ref`` then ``git diff --unified=0 <sha> -- *paths``.
    Returns False on any git error (fail-open: caller treats files as Bucket A triggers).
    Returns True if the diff is empty (no changes at all — version bump already absorbed).
    """
    try:
        merge_base_result = subprocess.run(
            ["git", "merge-base", "HEAD", base_ref],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        merge_base_sha = merge_base_result.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", merge_base_sha):
            return False

        diff_result = subprocess.run(
            ["git", "diff", "--unified=0", merge_base_sha, "--", *paths],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False

    for line in diff_result.stdout.splitlines():
        if not (line.startswith("+") or line.startswith("-")):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if not _VERSION_LINE_RE.match(line):
            return False  # non-version change found — not a pure version bump

    return True  # all diff lines are version strings (or diff is empty)


# Files in BUCKET_A_PATTERNS that may produce false positives from CI version bumps.
# These are given a content check before triggering a full run.
_VERSION_BUMP_FILES: frozenset[str] = frozenset({"pyproject.toml", "uv.lock"})


def check_bucket_a_content_aware(
    changed_files: set[str],
    cwd: str | Path,
    base_ref: str,
) -> bool:
    """Content-aware Bucket A check that skips version-bump-only changes.

    Identical to ``check_bucket_a`` except for ``pyproject.toml`` and ``uv.lock``:
    if those files are present in *changed_files* but their entire diff consists only
    of ``version = "..."`` line changes, they are NOT treated as Bucket A triggers.

    Falls back to treating them as Bucket A triggers on any git failure (fail-open).

    Other Bucket A patterns (conftest.py, _rules.py, etc.) are unaffected — they
    still trigger a full run immediately without any git diff inspection.
    """
    # Fast path: check all non-version-bump patterns first (no git I/O)
    non_version_files = changed_files - _VERSION_BUMP_FILES
    if check_bucket_a(non_version_files):
        return True

    # Check version-bump-candidate files that are also in BUCKET_A_PATTERNS
    version_hits = changed_files & _VERSION_BUMP_FILES & BUCKET_A_PATTERNS
    if not version_hits:
        return False  # no version-bump candidates hit Bucket A

    # Content check: if every diff line is a version string, skip Bucket A
    if _is_only_version_changes_in_diff(cwd, base_ref, *version_hits):
        return False  # version-only bump — not a structural change

    return True  # diff contains non-version changes → full run


def load_manifest(path: str | Path) -> dict[str, Any] | None:
    """Load .autoskillit/test-filter-manifest.yaml, or None if absent."""
    import yaml

    manifest_path = Path(path) / ".autoskillit" / "test-filter-manifest.yaml"
    if not manifest_path.exists():
        return None
    try:
        with manifest_path.open() as f:
            return yaml.safe_load(f)
    except OSError as exc:
        warnings.warn(f"Cannot read manifest {manifest_path}: {exc}", stacklevel=2)
        return None
    except yaml.YAMLError as exc:
        warnings.warn(f"Malformed YAML in {manifest_path}: {exc}", stacklevel=2)
        return None


def load_coverage_map(
    map_path: str | Path,
    max_age_days: int = 30,
) -> dict[str, set[str]] | None:
    """Load .autoskillit/test-source-map.json with staleness guard.

    Args:
        map_path: Path to the test-source-map.json file.
        max_age_days: Maximum age in days before the map is considered stale.
                      Defaults to 30 days.

    Returns:
        dict mapping source file paths to sets of test file paths, or None when:
        - The file does not exist
        - The file is older than max_age_days
        - The file cannot be read or parsed
    """
    map_path = Path(map_path)
    try:
        stat = map_path.stat()
    except OSError:
        return None

    age = datetime.datetime.now().timestamp() - stat.st_mtime
    if age > max_age_days * 24 * 3600:
        return None

    try:
        raw = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.warn(f"Cannot read coverage map {map_path}: {exc}", stacklevel=2)
        return None

    if not isinstance(raw, dict):
        warnings.warn(f"Coverage map {map_path} is not a JSON object", stacklevel=2)
        return None

    result: dict[str, set[str]] = {}
    for src, tests in raw.items():
        if not isinstance(tests, list):
            warnings.warn(
                f"Coverage map {map_path}: value for {src!r} is not a list",
                stacklevel=2,
            )
            return None
        result[src] = set(tests)
    return result


def apply_manifest(
    changed_files: set[str],
    manifest: dict[str, Any] | None,
) -> set[str] | None:
    """Return test directories matched by manifest patterns for the changed files.

    Returns None when manifest is None (fail-open) or when any changed file matches
    no manifest pattern (fail-open: caller should run the full suite).
    """
    if manifest is None:
        return None
    compiled = {pat: pathspec.PathSpec.from_lines("gitwildmatch", [pat]) for pat in manifest}
    matched_dirs: set[str] = set()
    for f in changed_files:
        file_matched = False
        for pattern, spec in compiled.items():
            if spec.match_file(f):
                test_dirs = manifest[pattern]
                if isinstance(test_dirs, list):
                    matched_dirs.update(test_dirs)
                elif isinstance(test_dirs, str):
                    matched_dirs.add(test_dirs)
                file_matched = True
        if not file_matched:
            return None
    return matched_dirs


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
                        bare = mod.split(".")[-1] if "." in mod else mod.lstrip(".")
                        if bare == module_name:
                            rel_init = str(init_path.relative_to(src_root))
                            expanded.add(rel_init)
                            break
                except SyntaxError as exc:
                    warnings.warn(
                        f"Failed to parse {init_path}: {exc}",
                        stacklevel=2,
                    )
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
    coverage_map_path: str | Path | None = None,
    cwd: str | Path | None = None,
    base_ref: str | None = None,
) -> set[Path] | None:
    """Compute the set of test paths to run, or None for a full run.

    Algorithm:
    1. None changed_files -> None (fail-open)
    2. >30 files -> None (large changeset)
    3. Bucket A triggered -> None (full run)
    4. Classify: src Python -> cascade, test Python -> direct, non-Python -> manifest
    5. Compute always-run set for mode (includes arch/contracts for both modes)
    6. Union all sets
    7. For aggressive mode -> coverage oracle file-level refinement
    8. Resolve to concrete paths
    """
    if mode == FilterMode.NONE:
        return None

    if changed_files is None:
        return None

    if len(changed_files) > _LARGE_CHANGESET_THRESHOLD:
        return None

    if cwd is not None and base_ref is not None:
        if check_bucket_a_content_aware(changed_files, cwd, base_ref):
            return None
        # Exclude version-bump files that passed the content-aware check from classification.
        # Recomputes the same set as `version_hits` inside check_bucket_a_content_aware because
        # that function returns bool; extracting the set here avoids changing its signature.
        version_bump_in_bucket_a = changed_files & _VERSION_BUMP_FILES & BUCKET_A_PATTERNS
        if version_bump_in_bucket_a:
            changed_files = changed_files - version_bump_in_bucket_a
    else:
        if check_bucket_a(changed_files):
            return None

    tests_root = Path(tests_root)

    cascade_map = (
        LAYER_CASCADE_CONSERVATIVE if mode == FilterMode.CONSERVATIVE else LAYER_CASCADE_AGGRESSIVE
    )
    always_run = (
        ALWAYS_RUN_CONSERVATIVE if mode == FilterMode.CONSERVATIVE else ALWAYS_RUN_AGGRESSIVE
    )

    test_dirs: set[str] = set()
    direct_test_files: set[str] = set()
    for f in changed_files:
        if f.startswith("tests/") and f.endswith(".py"):
            direct_test_files.add(f)
        elif f.startswith("src/") and f.endswith(".py"):
            pkg = _file_to_package(f)
            if pkg == "core" and mode == FilterMode.CONSERVATIVE:
                stem = Path(f).stem
                if stem in _CORE_UNIVERSAL_MODULES or stem == "__init__":
                    test_dirs.update(cascade_map["core"])
                elif stem in MODULE_CASCADE_CORE:
                    test_dirs.update(MODULE_CASCADE_CORE[stem])
                else:
                    test_dirs.update(cascade_map["core"])  # fail-open: unknown stem
            elif pkg and pkg in cascade_map:
                test_dirs.update(cascade_map[pkg])
            else:
                return None
        elif f.endswith(".py"):
            return None
        else:
            manifest_dirs = apply_manifest({f}, manifest)
            if manifest_dirs is None:
                return None
            test_dirs.update(manifest_dirs)

    # Expand src Python files via re-export closure: add __init__.py files that
    # directly re-export any of the changed modules, then cascade-classify them.
    changed_src_py = {f for f in changed_files if f.startswith("src/") and f.endswith(".py")}
    if changed_src_py:
        try:
            expanded = _expand_reexport_closure(changed_src_py, tests_root.parent)
            for f in expanded - changed_src_py:
                if f.startswith("src/") and f.endswith(".py"):
                    pkg = _file_to_package(f)
                    if pkg == "core" and mode == FilterMode.CONSERVATIVE:
                        stem = Path(f).stem
                        if stem in _CORE_UNIVERSAL_MODULES or stem == "__init__":
                            test_dirs.update(cascade_map["core"])
                        elif stem in MODULE_CASCADE_CORE:
                            test_dirs.update(MODULE_CASCADE_CORE[stem])
                        else:
                            test_dirs.update(cascade_map["core"])  # fail-open: unknown stem
                    elif pkg and pkg in cascade_map:
                        test_dirs.update(cascade_map[pkg])
        except Exception:
            logging.getLogger(__name__).debug(  # noqa: TID251
                "_expand_reexport_closure suppressed", exc_info=True
            )  # fail-open: expansion errors do not affect the computed scope

    if mode == FilterMode.CONSERVATIVE and changed_files:
        # REQ-TIER-001: arch and contracts always unconditional
        test_dirs.update(_ALWAYS_RUN_CONSERVATIVE_UNCONDITIONAL)

        # REQ-TIER-002: docs gated on documentation file changes
        if any(
            f.startswith(_DOCS_TRIGGER_PREFIX) or f in _DOCS_TRIGGER_FILES for f in changed_files
        ):
            test_dirs.add("docs")
        else:
            direct_test_files.add(str(tests_root / "docs" / "test_doc_counts.py"))

        # REQ-TIER-003: 9 structural infra files always; full infra dir only on trigger
        for fname in _INFRA_UNCONDITIONAL_FILES:
            direct_test_files.add(str(tests_root / "infra" / fname))
        if any(
            f.startswith(_INFRA_HOOK_TRIGGER_PREFIX)
            or f.startswith(_INFRA_CI_TRIGGER_PREFIX)
            or f in _INFRA_CI_TRIGGER_FILES
            for f in changed_files
        ):
            test_dirs.add("infra")
    else:
        # REQ-TIER-004: fail-open for empty changeset; aggressive mode uses its own set
        test_dirs.update(always_run)

    # Step 7: Aggressive mode file-level refinement via coverage oracle.
    # Only runs when mode is AGGRESSIVE and a coverage map path is provided.
    # Falls back to directory-level entirely if oracle is stale or missing (cov_map is None).
    if mode == FilterMode.AGGRESSIVE and coverage_map_path is not None:
        cov_map = load_coverage_map(coverage_map_path)
        if cov_map is not None:
            # Group changed source files by the cascade directories they contribute.
            # dir_to_src_files[d] = set of src files whose cascade includes dir d.
            dir_to_src_files: dict[str, set[str]] = {}
            for f in changed_src_py:
                pkg = _file_to_package(f)
                if pkg and pkg in cascade_map:
                    for d in cascade_map[pkg]:
                        dir_to_src_files.setdefault(d, set()).add(f)

            # For each cascade dir: if ALL contributing source files are present in the
            # coverage map (with non-empty test file sets), replace the directory with
            # the union of their specific test files. If any file lacks coverage data or
            # has an empty set, keep the directory entry (fail-open).
            for d, src_files in dir_to_src_files.items():
                if d in test_dirs and all(f in cov_map and cov_map[f] for f in src_files):
                    test_dirs.discard(d)
                    for f in src_files:
                        direct_test_files.update(str(tests_root.parent / fp) for fp in cov_map[f])

    result: set[Path] = set()
    for d in test_dirs:
        dir_path = tests_root / d
        if dir_path.is_dir() or dir_path.is_file():
            result.add(dir_path)

    for f in direct_test_files:
        result.add(Path(f))

    return result
