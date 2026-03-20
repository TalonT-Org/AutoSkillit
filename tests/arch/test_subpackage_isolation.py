"""L1/L2/L3 sub-package isolation, __all__ completeness, size/file-count constraints.

Tests:
  - Sync manifest deletion checks
  - Singleton definition locality
  - Module-level I/O ban
  - Severity and SKILL_TOOLS placement
  - CLAUDE.md documentation coverage
  - Sub-package importability checks (T1–T9 + old-module-deleted + package checks)
  - REQ-CNST: size limits, file count limits, core isolation, isolated module isolation
  - Tool handler business-logic ban
  - ToolContext Protocol type enforcement
  - make_context wiring completeness
  - __all__ completeness
  - recipe/rules.py, recipe/_api.py, migration/_api.py existence
  - migration/engine.py no module-level recipe imports
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.arch._helpers import (
    _SOURCE_FILES,
    SRC_ROOT,
    _extract_module_level_internal_imports,
    _is_mcp_tool_decorator,
    _rel,
)
from tests.arch._rules import RuleDescriptor

# ── REQ-ARCH-002 descriptor ───────────────────────────────────────────────────

ISOLATION_RULES: dict[str, RuleDescriptor] = {
    "REQ-ARCH-002": RuleDescriptor(
        rule_id="REQ-ARCH-002",
        name="tool-context-service-fields-use-protocol-types",
        lens="module-dependency",
        description=(
            "Every non-exempt ToolContext service field must be annotated with a Protocol "
            "type from autoskillit.core.types, not a concrete implementation class."
        ),
        rationale=(
            "Protocol-typed fields enable dependency injection and make the context "
            "independently testable without importing concrete server or execution classes."
        ),
        exemptions=frozenset({"plugin_dir", "config"}),  # non-service structural fields
        severity="high",
        defense_standard="DS-008",
    ),
}


def _get_call_func_name(node: ast.Call) -> str | None:
    """Return the function name for simple calls, or None for complex expressions."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


# ── Rule 2: Singleton definition locality ─────────────────────────────────────
# "server" allows mcp = FastMCP(...); "cli" allows app = App(...) etc.
SINGLETON_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "__init__",  # server/__init__.py: mcp = FastMCP(...)
        "app",  # cli/app.py: app = App(...), config_app = App(...), etc.
        "store",  # migration/store.py: defensive exemption for future module-level construction
        "validator",  # recipe/validator.py: defensive exemption for decorator-based rule registry
    }
)
_SINGLETON_SAFE_CALL_NAMES: frozenset[str] = frozenset(
    {
        "frozenset",
        "set",
        "list",
        "dict",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "type",
        "TypeVar",
        "field",
        "dataclass",
        "get_logger",
        "getLogger",  # stdlib logging.getLogger — safe module-level logger registration
        "Lock",  # threading.Lock — safe module-level thread-safety primitive
        "version",
        "compile",
        "object",
    }
)

# ── Rule 4: No module-level I/O ───────────────────────────────────────────────
_MODULE_LEVEL_IO_FUNC_NAMES: frozenset[str] = frozenset({"load_config", "open", "yaml.safe_load"})
_MODULE_LEVEL_IO_ATTR_CALLS: frozenset[tuple[str, str]] = frozenset(
    {("Path", "cwd"), ("os", "getcwd")}
)
_MODULE_LEVEL_IO_EXEMPT: frozenset[str] = frozenset({"__main__.py"})


def _scan_module_level_io(path: Path) -> list[tuple[int, int, str]]:
    """Return (lineno, col, message) tuples for module-level I/O calls in path.

    Scans only tree.body (direct module-level statements). Does not descend
    into nested function or class definitions.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[tuple[int, int, str]] = []
    for stmt in tree.body:
        # Skip function/class definitions — their bodies are not module-level I/O
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # Walk only the direct statement (not recursing into nested scopes)
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Simple name calls: open(), load_config()
            if isinstance(func, ast.Name) and func.id in _MODULE_LEVEL_IO_FUNC_NAMES:
                violations.append(
                    (node.lineno, node.col_offset, f"module-level I/O call: {func.id}()")
                )
            # Attribute calls: yaml.safe_load(), Path.cwd(), os.getcwd()
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                obj = func.value.id
                attr = func.attr
                if (obj, attr) in _MODULE_LEVEL_IO_ATTR_CALLS:
                    violations.append(
                        (
                            node.lineno,
                            node.col_offset,
                            f"module-level I/O call: {obj}.{attr}()",
                        )
                    )
                elif attr == "safe_load" and obj == "yaml":
                    violations.append(
                        (
                            node.lineno,
                            node.col_offset,
                            "module-level I/O call: yaml.safe_load()",
                        )
                    )
    return violations


def _get_module_ast(filename: str) -> ast.Module:
    return ast.parse((SRC_ROOT / filename).read_text())


def _top_level_class_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.col_offset == 0
    }


def _top_level_assign_targets(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_sync_manifest_module_deleted():
    """REQ-SYNC-002: sync_manifest.py does not exist."""
    sync_path = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "sync_manifest.py"
    assert not sync_path.exists()


def test_no_sync_manifest_imports_in_production_code():
    """REQ-SYNC-001: No production module imports from autoskillit.sync_manifest."""
    src_dir = Path(__file__).parent.parent.parent / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        assert "sync_manifest" not in content, f"Found sync_manifest reference in {py_file}"


# ── Rule 2: test_singleton_definition_locality ────────────────────────────────


@pytest.mark.parametrize("source_file", _SOURCE_FILES)
def test_singleton_definition_locality(source_file: Path) -> None:
    """Module-level constructor calls are only permitted in SINGLETON_ALLOWED_MODULES."""
    mod_stem = source_file.stem
    if mod_stem in SINGLETON_ALLOWED_MODULES:
        pytest.skip(f"{mod_stem!r} is in SINGLETON_ALLOWED_MODULES")

    tree = ast.parse(source_file.read_text())
    violations: list[str] = []
    for node in tree.body:  # module-level only
        rhs: ast.expr | None = None
        if isinstance(node, ast.Assign) and node.value:
            rhs = node.value
        elif isinstance(node, ast.AnnAssign) and node.value:
            rhs = node.value
        if rhs is None or not isinstance(rhs, ast.Call):
            continue
        func_name = _get_call_func_name(rhs)
        if func_name in _SINGLETON_SAFE_CALL_NAMES:
            continue
        if func_name is None:
            continue  # complex expression, skip
        violations.append(
            f"  line {node.lineno}: module-level call to '{func_name}()' — "
            f"add {mod_stem!r} to SINGLETON_ALLOWED_MODULES if intentional"
        )

    assert not violations, f"Singleton locality violations in {_rel(source_file)}:\n" + "\n".join(
        violations
    )


# ── Rule 4: test_no_module_level_io ───────────────────────────────────────────


@pytest.mark.parametrize(
    "source_file",
    [f for f in _SOURCE_FILES if f.name not in _MODULE_LEVEL_IO_EXEMPT],
)
def test_no_module_level_io(source_file: Path) -> None:
    """Production modules must not call open/load_config/yaml.safe_load at module scope."""
    violations = _scan_module_level_io(source_file)
    assert not violations, "Module-level I/O calls found:\n" + "\n".join(
        f"  {source_file}:{ln}:{col}: {msg}" for ln, col, msg in violations
    )


# ── Calibration tests ──────────────────────────────────────────────────────────


def test_singleton_locality_detects_non_allowed(tmp_path: Path) -> None:
    snippet = "class Foo: pass\nfoo = Foo()\n"
    f = tmp_path / "fake_module.py"
    f.write_text(snippet)
    tree = ast.parse(snippet)
    found = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func_name = _get_call_func_name(node.value)
            if func_name and func_name not in _SINGLETON_SAFE_CALL_NAMES:
                found = True
    assert found


def test_no_module_level_io_detects_open_call(tmp_path: Path) -> None:
    f = tmp_path / "fake.py"
    f.write_text("_f = open('config.yaml')\n")
    assert _scan_module_level_io(f)


def test_no_module_level_io_detects_yaml_load(tmp_path: Path) -> None:
    f = tmp_path / "fake.py"
    f.write_text("import yaml\n_data = yaml.safe_load(open('x'))\n")
    assert _scan_module_level_io(f)


# ── Severity and SKILL_TOOLS placement tests ──────────────────────────────────


def test_severity_defined_in_types():
    """Severity must be a top-level class in core/_type_enums.py (the enums sub-module)."""
    tree = _get_module_ast("core/_type_enums.py")
    assert "Severity" in _top_level_class_names(tree), (
        "Severity not found in core/_type_enums.py; it must be defined there"
    )


def test_skill_tools_defined_in_types():
    """SKILL_TOOLS must be a top-level assignment in core/_type_constants.py."""
    tree = _get_module_ast("core/_type_constants.py")
    assert "SKILL_TOOLS" in _top_level_assign_targets(tree), (
        "SKILL_TOOLS not found in core/_type_constants.py; it must be defined there"
    )


def test_pyproject_cyclopts_minimum_version() -> None:
    """cyclopts lower bound in pyproject.toml must be >=4.0, not >=3.0.

    cyclopts 3.x and 4.x have incompatible APIs. A >=3.0 constraint allows
    a conservative resolver to silently install 3.x, which fails at runtime.
    """
    import re

    toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    content = toml_path.read_text()
    match = re.search(r'"cyclopts>=([\d.]+)"', content)
    assert match is not None, "cyclopts dependency not found in pyproject.toml"
    major = int(match.group(1).split(".")[0])
    assert major >= 4, (
        f"cyclopts minimum version is {match.group(1)}, expected >=4.0. "
        "cyclopts 3.x API is incompatible with the 4.x API used in this codebase."
    )


def test_no_yaml_safe_load_in_migration_engine() -> None:
    """P7-2: ContractMigrationAdapter.validate must use _load_yaml, not yaml.safe_load."""
    src = (Path(__file__).parent.parent.parent / "src/autoskillit/migration/engine.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "safe_load":
                pytest.fail(
                    f"migration/engine.py line {node.lineno}: "
                    f"direct yaml.safe_load call found; use load_yaml from core.io instead"
                )


def test_pytest_asyncio_version_bound() -> None:
    """P11-2: pytest-asyncio lower bound must match the published 0.x stable series."""
    import tomllib

    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    deps = data["project"]["optional-dependencies"]["dev"]
    asyncio_dep = next(d for d in deps if d.startswith("pytest-asyncio"))
    assert ">=1.0.0" in asyncio_dep, f"Expected pytest-asyncio>=1.0.0, got: {asyncio_dep!r}"


def test_severity_not_defined_locally_in_recipe_validator() -> None:
    """Severity must be imported from types, not locally defined in recipe sub-modules."""
    for filename in ("recipe/validator.py", "recipe/contracts.py"):
        ast_module = _get_module_ast(filename)
        class_names = _top_level_class_names(ast_module)
        assert "Severity" not in class_names, (
            f"Severity must live in core/types.py, not {filename}"
        )


def test_severity_not_locally_defined_in_doctor() -> None:
    """cli/_doctor.py must not define its own Severity — it must import from core."""
    ast_module = _get_module_ast("cli/_doctor.py")
    class_names = _top_level_class_names(ast_module)
    assert "Severity" not in class_names, (
        "cli/_doctor.py must import Severity from autoskillit.core, not define it locally"
    )


def test_skill_tools_not_defined_in_recipe_io() -> None:
    """SKILL_TOOLS must not be defined locally in recipe/io.py."""
    ast_module = _get_module_ast("recipe/io.py")
    assigns = _top_level_assign_targets(ast_module)
    assert "SKILL_TOOLS" not in assigns and "_SKILL_TOOLS" not in assigns, (
        "SKILL_TOOLS must be imported from core/types, not defined in recipe/io.py"
    )


def test_skill_tools_not_defined_in_recipe_validator() -> None:
    """SKILL_TOOLS must not be defined locally in recipe/validator.py or recipe/contracts.py."""
    for filename in ("recipe/validator.py", "recipe/contracts.py"):
        ast_module = _get_module_ast(filename)
        assigns = _top_level_assign_targets(ast_module)
        assert "SKILL_TOOLS" not in assigns and "_SKILL_TOOLS" not in assigns, (
            f"SKILL_TOOLS must be imported from core/types, not defined in {filename}"
        )


def test_contract_validator_module_deleted() -> None:
    """contract_validator.py must not exist — functionality merged into recipe_validator.py."""
    cv_path = SRC_ROOT / "contract_validator.py"
    assert not cv_path.exists(), (
        "contract_validator.py should be deleted; its code lives in recipe_validator.py"
    )


def test_recipe_validator_has_regex_patterns() -> None:
    """recipe/contracts.py must define context/input regex patterns."""
    ast_module = _get_module_ast("recipe/contracts.py")
    assigns = _top_level_assign_targets(ast_module)
    assert "_CONTEXT_REF_RE" in assigns, "recipe/contracts.py must define _CONTEXT_REF_RE"
    assert "_INPUT_REF_RE" in assigns, "recipe/contracts.py must define _INPUT_REF_RE"


def test_recipe_validator_no_process_lifecycle_import() -> None:
    """recipe/validator.py and recipe/contracts.py must not import from process_lifecycle."""
    for filename in ("recipe/validator.py", "recipe/contracts.py"):
        import_pairs = _extract_module_level_internal_imports(SRC_ROOT / filename)
        import_stems = [stem for stem, _ in import_pairs]
        assert "process_lifecycle" not in import_stems, (
            f"{filename} must not import from process_lifecycle"
        )


def test_server_uses_recipe_io_not_recipe_loader_for_discovery() -> None:
    """server/ package must import recipe discovery from recipe.io, not from recipe.loader."""
    server_dir = SRC_ROOT / "server"
    combined_src = "\n".join(p.read_text() for p in server_dir.glob("*.py"))
    assert (
        "from autoskillit.recipe.io import" in combined_src
        or "from .recipe.io import" in combined_src
        or "from autoskillit.recipe import" in combined_src
    ), "server/ package must import recipe discovery functions from recipe.io or recipe package"
    assert "from autoskillit.recipe.loader import list_recipes" not in combined_src
    assert "from autoskillit.recipe.loader import load_recipe" not in combined_src


# ── New L2 sub-package tests (T1–T7 from groupC plan) ─────────────────────────


def test_recipe_subpackage_importable() -> None:
    """T1: recipe/ package exposes all expected symbols."""
    from autoskillit.recipe import (  # noqa: F401
        Recipe,
        RecipeStep,
        analyze_dataflow,
        check_contract_staleness,
        find_recipe_by_name,
        generate_recipe_card,
        iter_steps_with_context,
        list_recipes,
        load_bundled_manifest,
        load_recipe,
        load_recipe_card,
        run_semantic_rules,
        validate_recipe,
        validate_recipe_cards,
    )


def test_contracts_module_has_staleitem() -> None:
    """T2: recipe/contracts.py exposes StaleItem and load_bundled_manifest."""
    from autoskillit.recipe.contracts import StaleItem, load_bundled_manifest  # noqa: F401


def test_validator_module_has_validate() -> None:
    """T3: recipe/validator.py exposes validate_recipe, run_semantic_rules, analyze_dataflow."""
    from autoskillit.recipe.validator import (  # noqa: F401
        analyze_dataflow,
        run_semantic_rules,
        validate_recipe,
    )


def test_migration_subpackage_importable() -> None:
    """T4: migration/ package exposes MigrationEngine, applicable_migrations, FailureStore."""
    from autoskillit.migration import (  # noqa: F401
        FailureStore,
        MigrationEngine,
        applicable_migrations,
    )

    assert MigrationEngine is not None
    assert applicable_migrations is not None
    assert FailureStore is not None


def test_llm_triage_imports_from_contracts_not_validator() -> None:
    """T7: REQ-DSGN-007 — _llm_triage.py imports contract types, not recipe/validator.

    Accepts both direct sub-module import (recipe.contracts) and gateway import
    (autoskillit.recipe) since REQ-IMP-001 requires gateway imports for non-server/cli files.
    """
    src = (SRC_ROOT / "_llm_triage.py").read_text()
    assert (
        "recipe.contracts" in src
        or "recipe/contracts" in src
        or "from autoskillit.recipe import" in src
    ), "_llm_triage.py must import contract types from recipe package"
    assert "recipe.validator" not in src and "recipe_validator" not in src, (
        "_llm_triage.py must not import from recipe.validator or old recipe_validator"
    )


def test_old_flat_recipe_modules_removed() -> None:
    """T9a: old flat recipe modules must be deleted after sub-package migration."""
    for name in ("recipe_schema.py", "recipe_io.py", "recipe_loader.py", "recipe_validator.py"):
        assert not (SRC_ROOT / name).exists(), (
            f"{name} should be removed — code now lives in recipe/ sub-package"
        )


def test_old_flat_migration_modules_removed() -> None:
    """T9b: old flat migration modules must be deleted after sub-package migration."""
    for name in ("migration_engine.py", "migration_loader.py", "failure_store.py"):
        assert not (SRC_ROOT / name).exists(), (
            f"{name} should be removed — code now lives in migration/ sub-package"
        )


# ── New L3 package tests (groupD plan) ────────────────────────────────────────


def test_server_is_package() -> None:
    """server/ must be a package directory, not a flat module."""
    assert (SRC_ROOT / "server").is_dir(), "server/ directory must exist"
    assert (SRC_ROOT / "server" / "__init__.py").exists()
    assert not (SRC_ROOT / "server.py").exists(), "server.py flat module must be deleted"


def test_cli_is_package() -> None:
    """cli/ must be a package directory, not a flat module."""
    assert (SRC_ROOT / "cli").is_dir(), "cli/ directory must exist"
    assert (SRC_ROOT / "cli" / "__init__.py").exists()
    assert not (SRC_ROOT / "cli.py").exists(), "cli.py flat module must be deleted"


def test_server_file_count_under_limit() -> None:
    """server/ must not exceed 16 Python files (REQ-DSGN-002).

    Limit updated from 14 to 16 after tools_integrations was split into
    tools_github, tools_issue_lifecycle, and tools_pr_ops.
    """
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= 16, f"server/ has {len(py_files)} files, max is 16"


def test_tools_integrations_replaced_by_split_modules() -> None:
    """tools_integrations.py deleted; three replacement modules exist."""
    server = SRC_ROOT / "server"
    assert not (server / "tools_integrations.py").exists()
    assert (server / "tools_github.py").exists()
    assert (server / "tools_issue_lifecycle.py").exists()
    assert (server / "tools_pr_ops.py").exists()


def test_split_files_under_750_lines() -> None:
    """Each split module must stay under the 750-line threshold."""
    server = SRC_ROOT / "server"
    for name in ("tools_github.py", "tools_issue_lifecycle.py", "tools_pr_ops.py"):
        lines = len((server / name).read_text().splitlines())
        assert lines <= 750, f"{name} has {lines} lines, exceeds 750"


def test_extract_block_moved_to_helpers() -> None:
    """_extract_block moved to server/helpers.py after tools_integrations split."""
    from autoskillit.server.helpers import _extract_block

    assert callable(_extract_block)


def test_all_tools_importable_from_split_modules() -> None:
    """All 9 tools are importable from their new home modules."""
    from autoskillit.server.tools_github import fetch_github_issue, get_issue_title, report_bug
    from autoskillit.server.tools_issue_lifecycle import (
        claim_issue,
        enrich_issues,
        prepare_issue,
        release_issue,
    )
    from autoskillit.server.tools_pr_ops import bulk_close_issues, get_pr_reviews

    for name, fn in [
        ("fetch_github_issue", fetch_github_issue),
        ("get_issue_title", get_issue_title),
        ("report_bug", report_bug),
        ("prepare_issue", prepare_issue),
        ("enrich_issues", enrich_issues),
        ("claim_issue", claim_issue),
        ("release_issue", release_issue),
        ("get_pr_reviews", get_pr_reviews),
        ("bulk_close_issues", bulk_close_issues),
    ]:
        assert callable(fn), f"{name} is not callable"


def test_git_operations_moved_to_server_package() -> None:
    """git_operations.py must be removed; its logic lives in server/git.py."""
    assert not (SRC_ROOT / "git_operations.py").exists()
    assert (SRC_ROOT / "server" / "git.py").exists()


def test_doctor_moved_to_cli_package() -> None:
    """_doctor.py must be removed; its logic lives in cli/_doctor.py."""
    assert not (SRC_ROOT / "_doctor.py").exists()
    assert (SRC_ROOT / "cli" / "_doctor.py").exists()


# ── New REQ-CNST tests (groupE) ───────────────────────────────────────────────


def test_test_suite_has_domain_subdirectories():
    """All 12 domain-aligned test subdirectories exist after groupE reorganization."""
    tests_root = Path(__file__).parent.parent
    expected = [
        "core",
        "config",
        "pipeline",
        "execution",
        "workspace",
        "recipe",
        "migration",
        "server",
        "cli",
        "arch",
        "contracts",
        "infra",
    ]
    missing = [d for d in expected if not (tests_root / d / "__init__.py").exists()]
    assert not missing, f"Missing test subdirectories (run groupE): {missing}"


def test_test_suite_oversized_files_split():
    """No test file at tests/ root exceeds 1,000 lines after groupE split."""
    tests_root = Path(__file__).parent.parent
    over = [
        f"{f.name} ({len(f.read_text().splitlines())} lines)"
        for f in tests_root.glob("test_*.py")
        if len(f.read_text().splitlines()) > 1000
    ]
    assert not over, f"Oversized test files remain (run groupE): {over}"


def test_tmp_path_has_worktree_hash(tmp_path: Path) -> None:
    """tmp_path must contain a .ROOT_DIR-derived hash to prevent cross-worktree collision.

    Fails when pytest is invoked with --basetemp=/dev/shm/pytest-tmp (static path).
    Passes only when Taskfile.yml derives PYTEST_TMPDIR from .ROOT_DIR via the
    slim-sprig sha256sum template function.
    """
    if sys.platform == "linux":
        import hashlib
        import os

        cwd_hash = hashlib.sha256(os.getcwd().encode()).hexdigest()[:8]
        path_str = str(tmp_path)
        assert f"pytest-tmp-{cwd_hash}" in path_str, (
            f"tmp_path ({path_str!r}) does not contain the expected worktree hash "
            f"'{cwd_hash}'. PYTEST_TMPDIR must be derived from .ROOT_DIR. "
            f"Expected /dev/shm/pytest-tmp-{cwd_hash} as the base. "
            "Update Taskfile.yml PYTEST_TMPDIR to use a .ROOT_DIR-derived hash suffix "
            "(use slim-sprig: {{ substr 0 8 (sha256sum .ROOT_DIR) }})."
        )


def test_no_subpackage_exceeds_10_files() -> None:
    """REQ-CNST-003: No sub-package directory may contain more than 10 Python files.

    Exemptions (rule ID | rationale):
      server/ — REQ-CNST-003-E1: server/ splits tool handlers into per-domain files
        (tools_clone, tools_github, tools_issue_lifecycle, tools_pr_ops, tools_ci,
        tools_git, tools_recipe, tools_status, tools_workspace, tools_execution,
        tools_kitchen, helpers, git, _factory, _state, __init__); each file is a
        thin routing layer. Exempt at 16 files.
      recipe/ — REQ-CNST-003-E2: recipe/ hosts one file per semantic-rule domain
        (rules_bypass, rules_ci, rules_clone, etc.) for independent testability.
      execution/ — REQ-CNST-003-E3: execution/ decomposes process lifecycle into
        focused single-concern modules (_process_io, _process_kill, _process_race,
        etc.) that cannot be merged without re-introducing the coupling they isolate.
      core/ — REQ-CNST-003-E4: core/ types split into per-concern type modules
        (_type_enums, _type_protocols, _type_results, _type_subprocess, etc.) to
        prevent circular imports while keeping L0 types co-located. Exempt at 14 files.
    """
    EXEMPTIONS: dict[str, int] = {"server": 16, "recipe": 27, "execution": 23, "core": 14}
    violations: list[str] = []
    for sub_dir in sorted(SRC_ROOT.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_") or sub_dir.name == "__pycache__":
            continue
        py_files = list(sub_dir.glob("*.py"))
        limit = EXEMPTIONS.get(sub_dir.name, 10)
        if len(py_files) > limit:
            violations.append(f"{sub_dir.name}/: {len(py_files)} Python files (max {limit})")
    assert not violations, "Sub-packages exceeding 10 Python files:\n" + "\n".join(
        f"  {v}" for v in violations
    )


# ── REQ-CNST-010: Per-module source size limit ───────────────────────────────
# REQ-CNST-010: No source module in src/autoskillit/ may exceed 1000 lines.
# Modules that exceed this limit require a documented exemption with rule ID and
# rationale. Splitting is REQUIRED once a module exceeds 1000 lines.
#
# session.py (currently 864 lines) is deliberately NOT in this list because it
# is under the 1000-line limit. If it ever reaches 1000 lines, add it here —
# but first assess whether the adjudication pipeline has grown beyond its
# original single-responsibility scope (REQ-CNST-010-NOTE-1).

_LINE_LIMIT_EXEMPTIONS: dict[str, tuple[int, str]] = {
    # REQ-CNST-010-E1: core/types.py is the canonical type registry for the entire
    # package. It defines all StrEnums, protocols, constants, and shared type aliases
    # in one place to prevent circular imports across sub-packages. Exempt at 1200 lines.
    "types.py": (
        1200,
        "REQ-CNST-010-E1: canonical type registry — wide surface required to prevent "
        "circular imports; all enums/protocols/constants consolidated here",
    ),
}


def test_no_src_module_exceeds_line_limit() -> None:
    """REQ-CNST-010: No source module may exceed 1000 lines (exemptions require rule IDs).

    Exceptions are documented in _LINE_LIMIT_EXEMPTIONS with rationale.
    session.py (adjudication pipeline, ~864 lines) is intentionally near this
    limit; do NOT split below 1000 lines — see REQ-CNST-010-NOTE-1.
    """
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        line_count = len(py_file.read_text().splitlines())
        limit, _ = _LINE_LIMIT_EXEMPTIONS.get(py_file.name, (1000, ""))
        if line_count > limit:
            violations.append(
                f"{py_file.relative_to(SRC_ROOT)}: {line_count} lines (limit {limit})"
            )
    assert not violations, (
        "Source modules exceeding line limit "
        "(add entry to _LINE_LIMIT_EXEMPTIONS with rule ID + rationale):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_core_has_no_autoskillit_imports() -> None:
    """REQ-CNST-004: core/ modules must not import from any autoskillit sub-package."""
    core_dir = SRC_ROOT / "core"
    assert core_dir.exists(), "core/ package must exist"
    violations: list[str] = []
    for py_file in core_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    violations.append(f"core/{py_file.name}:{node.lineno}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "autoskillit" and len(parts) > 1:
                        violations.append(
                            f"core/{py_file.name}:{node.lineno}: imports {alias.name}"
                        )
    assert not violations, "core/ has autoskillit internal imports:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_isolated_modules_do_not_import_server_or_cli() -> None:
    """REQ-CNST-007: Root-level isolated modules must not import from server/ or cli/."""
    isolated = ["_llm_triage.py", "smoke_utils.py", "version.py"]
    forbidden_prefixes = ("autoskillit.server", "autoskillit.cli")
    violations: list[str] = []
    for filename in isolated:
        py_file = SRC_ROOT / filename
        if not py_file.exists():
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if any(mod == f or mod.startswith(f + ".") for f in forbidden_prefixes):
                    violations.append(f"{filename}:{node.lineno}: imports {mod}")
    assert not violations, "Root-level isolated modules import server/ or cli/:\n" + "\n".join(
        f"  {v}" for v in violations
    )


# ── REQ-CNST-008: Tool handler business-logic ban ────────────────────────────


def test_server_tool_handlers_have_no_business_logic() -> None:
    """REQ-CNST-008: @mcp.tool handler functions must contain no comprehensions or for-loops.

    Tool handlers must only: call _require_enabled(), delegate to domain functions,
    and return results. Comprehensions and for-loops indicate logic that belongs
    in a domain layer module.
    """
    server_dir = SRC_ROOT / "server"
    violations: list[str] = []
    for py_file in sorted(server_dir.glob("tools_*.py")):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                continue
            # Walk only the function body for business-logic patterns
            body_module = ast.Module(body=node.body, type_ignores=[])
            for child in ast.walk(body_module):
                if isinstance(child, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
                    violations.append(
                        f"server/{py_file.name}: {node.name}() line {child.lineno}: "
                        f"comprehension found — move to domain layer"
                    )
                elif isinstance(child, ast.For):
                    violations.append(
                        f"server/{py_file.name}: {node.name}() line {child.lineno}: "
                        f"for-loop found — move to domain layer"
                    )
    assert not violations, "Tool handlers contain business logic:\n" + "\n".join(
        f"  {v}" for v in violations
    )


# ── REQ-ARCH-002: ToolContext service fields use Protocol types ───────────────


def test_tool_context_service_fields_use_protocol_types() -> None:
    """REQ-ARCH-002: Every non-exempt ToolContext field must use a Protocol from core/types.py.

    Exempt fields:
    - plugin_dir: str primitive (explicitly stated in the requirement)
    - config: AutomationConfig dataclass (configuration container, not a service interface)
    """
    AUTOSKILLIT_ROOT = SRC_ROOT

    # Collect Protocol class names from core/types.py and its sub-modules via AST.
    # After the types.py split, Protocol definitions live in _type_protocols.py and
    # SubprocessRunner lives in _type_subprocess.py; types.py is a thin re-export hub.
    core_protocols: set[str] = set()
    for types_filename in (
        "core/types.py",
        "core/_type_protocols.py",
        "core/_type_subprocess.py",
    ):
        types_path = AUTOSKILLIT_ROOT / types_filename
        if not types_path.exists():
            continue
        types_tree = ast.parse(types_path.read_text())
        for node in ast.walk(types_tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_str = ast.unparse(base)
                    if "Protocol" in base_str:
                        core_protocols.add(node.name)
                        break

    # Collect ToolContext field annotations via AST
    context_path = AUTOSKILLIT_ROOT / "pipeline" / "context.py"
    context_tree = ast.parse(context_path.read_text())

    EXEMPT = {"plugin_dir", "config"}
    violations: list[str] = []

    for node in ast.walk(context_tree):
        if isinstance(node, ast.ClassDef) and node.name == "ToolContext":
            for item in node.body:
                if not isinstance(item, ast.AnnAssign):
                    continue
                field_name = ast.unparse(item.target)
                if field_name in EXEMPT:
                    continue

                # Collect all type names from annotation (unwraps Union/Optional)
                ann_str = ast.unparse(item.annotation)
                # Strip Optional[...] / X | None wrappers; collect bare names
                type_names = {
                    n.strip().strip("[]")
                    for n in ann_str.replace("|", ",").split(",")
                    if n.strip() not in ("None", "")
                }
                # Remove generic parameters, e.g. "list[str]" → "list"
                type_names = {n.split("[")[0] for n in type_names}

                for type_name in type_names:
                    if type_name not in core_protocols and type_name not in (
                        "str",
                        "int",
                        "float",
                        "bool",
                        "bytes",
                        "None",
                    ):
                        violations.append(
                            f"ToolContext.{field_name}: '{type_name}' is not a "
                            f"Protocol in core/types.py"
                        )

    assert not violations, (
        "ToolContext fields use concrete types instead of core/types.py Protocols:\n"
        + "\n".join(violations)
    )


def test_make_context_wires_all_optional_toolcontext_fields() -> None:
    """REQ-ARCH-002: make_context() must assign every optional ToolContext field.

    Self-closing: parses server/_factory.py via AST to discover all field assignments
    inside make_context(), then cross-checks against all ToolContext fields that have
    field(default=None). Fails if any optional field exists in ToolContext but is
    neither assigned in the ToolContext() constructor call nor in a post-construction
    assignment within make_context().
    """
    from autoskillit.pipeline.context import ToolContext

    # All optional service fields (field(default=None))
    optional_field_names = {
        name for name, f in ToolContext.__dataclass_fields__.items() if f.default is None
    }

    # Parse server/_factory.py via AST
    factory_path = SRC_ROOT / "server" / "_factory.py"
    tree = ast.parse(factory_path.read_text())

    # Find make_context() function body
    assigned_fields: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "make_context"):
            continue
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            # Capture keyword args in ToolContext(...) constructor call
            if isinstance(stmt, ast.Call):
                func_str = ast.unparse(stmt.func)
                if "ToolContext" in func_str:
                    for kw in stmt.keywords:
                        if kw.arg:
                            assigned_fields.add(kw.arg)
            # Capture post-construction assignments: ctx.field_name = ...
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        assigned_fields.add(target.attr)

    unwired = optional_field_names - assigned_fields
    assert not unwired, (
        f"make_context() does not assign these optional ToolContext fields: {unwired}. "
        "Add wiring in server/_factory.py make_context()."
    )


# ── groupC Part A tests ───────────────────────────────────────────────────────


def test_semantic_rule_functions_defined_in_rule_submodules() -> None:
    """P8: Semantic rule functions must be defined in rules_*.py submodules."""
    from autoskillit.recipe.validator import _check_outdated_version

    assert _check_outdated_version.__module__ == "autoskillit.recipe.rules_inputs"


def test_installed_version_in_core_types() -> None:
    """P3-F2: AUTOSKILLIT_INSTALLED_VERSION must be in autoskillit.core."""
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    assert isinstance(AUTOSKILLIT_INSTALLED_VERSION, str) and AUTOSKILLIT_INSTALLED_VERSION


def test_rule_submodules_no_autoskillit_init_import() -> None:
    """P3-F2: rules_*.py submodules must not import from autoskillit top-level __init__."""
    rule_files = sorted((SRC_ROOT / "recipe").glob("rules_*.py"))
    assert len(rule_files) >= 5, f"Expected >=5 rules_*.py files, found {len(rule_files)}"
    for rules_path in rule_files:
        assert "from autoskillit import __version__" not in rules_path.read_text(), (
            f"{rules_path.name} must not import from autoskillit top-level __init__"
        )


def test_recipe_api_module_exists() -> None:
    """P14-F1/F2: recipe/_api.py must exist and be importable."""
    import autoskillit.recipe._api  # noqa: F401


def test_default_recipe_repository_in_repository_module() -> None:
    """P2-F1: DefaultRecipeRepository must live in recipe/repository.py."""
    from autoskillit.recipe.repository import DefaultRecipeRepository  # noqa: F401


def test_default_recipe_repository_not_in_io() -> None:
    """P2-F1: DefaultRecipeRepository must be removed from recipe/io.py."""
    io_path = SRC_ROOT / "recipe" / "io.py"
    assert "class DefaultRecipeRepository" not in io_path.read_text()


def test_migration_api_module_exists() -> None:
    """P14-F3: migration/_api.py must exist and be importable."""
    import autoskillit.migration._api  # noqa: F401


def test_migration_engine_no_module_level_recipe_imports() -> None:
    """P4-F1: migration/engine.py must have no module-level recipe imports."""
    engine_path = SRC_ROOT / "migration" / "engine.py"
    recipe_violations = [
        (stem, ln)
        for stem, ln in _extract_module_level_internal_imports(engine_path)
        if stem == "recipe"
    ]
    assert not recipe_violations, f"module-level recipe imports remain: {recipe_violations}"


def test_only_yaml_imports_yaml_directly() -> None:
    """Only core/io.py may contain 'import yaml' at any scope."""
    src_dir = SRC_ROOT
    allowed_rel = str(Path("core") / "io.py")
    violations = []
    for py_file in sorted(src_dir.rglob("*.py")):
        rel = str(py_file.relative_to(src_dir))
        if rel == allowed_rel:
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "yaml" or alias.name.startswith("yaml."):
                        violations.append(f"{rel}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("yaml"):
                    violations.append(f"{rel}: from {node.module} import ...")
    assert not violations, f"Direct yaml imports found outside core/io.py: {violations}"


class TestGroupCMigration:
    """REQ-SIG-001..008: anyio task group replaces asyncio task scaffolding."""

    def test_no_asyncio_create_task(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.create_task(" not in source  # REQ-SIG-001

    def test_no_asyncio_wait_call(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "asyncio.wait(" not in source  # REQ-SIG-001

    def test_no_asyncio_import_at_runtime(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "import asyncio" not in source  # REQ-SIG-001

    def test_anyio_create_task_group_present(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "anyio.create_task_group()" in source  # REQ-SIG-002

    def test_scan_done_signals_absent(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "def scan_done_signals(" not in source  # REQ-SIG-003

    def test_race_accumulator_present(self):
        source = Path("src/autoskillit/execution/_process_race.py").read_text()
        assert "class RaceAccumulator" in source  # REQ-SIG-003

    def test_cancel_scope_cancel_present(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "cancel_scope.cancel()" in source  # REQ-SIG-004

    def test_resolve_termination_preserved(self):
        source = Path("src/autoskillit/execution/_process_race.py").read_text()
        assert "def resolve_termination(" in source  # REQ-SIG-005

    def test_channel_b_drain_wait_uses_move_on_after(self):
        source = Path("src/autoskillit/execution/process.py").read_text()
        assert "anyio.move_on_after(" in source  # REQ-SIG-006

    def test_watch_process_present(self):
        source = Path("src/autoskillit/execution/_process_race.py").read_text()
        assert "async def _watch_process(" in source  # REQ-SIG-007

    def test_watch_heartbeat_present(self):
        source = Path("src/autoskillit/execution/_process_race.py").read_text()
        assert "async def _watch_heartbeat(" in source  # REQ-SIG-007

    def test_watch_session_log_present(self):
        source = Path("src/autoskillit/execution/_process_race.py").read_text()
        assert "async def _watch_session_log(" in source  # REQ-SIG-007

    def test_race_signals_fields_unchanged(self):
        import dataclasses

        from autoskillit.execution.process import RaceSignals

        fields = {f.name for f in dataclasses.fields(RaceSignals)}
        assert fields == {
            "process_exited",
            "process_returncode",
            "channel_a_confirmed",
            "channel_b_status",
            "channel_b_session_id",
            "stdout_session_id",
        }  # REQ-SIG-008

    def test_race_signals_still_frozen(self):
        import dataclasses

        import pytest

        from autoskillit.execution.process import RaceSignals

        assert dataclasses.fields(RaceSignals)  # confirms it's a dataclass
        sig = RaceSignals(
            process_exited=False,
            process_returncode=None,
            channel_a_confirmed=False,
            channel_b_status=None,
            channel_b_session_id="",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            sig.process_exited = True  # REQ-SIG-008: frozen=True preserved


def test_pipeline_fidelity_module_deleted():
    """P2-F1: pipeline/fidelity.py must not exist after groupB."""
    import pytest

    with pytest.raises(ModuleNotFoundError):
        import autoskillit.pipeline.fidelity  # noqa: F401


def test_pipeline_pr_gates_no_longer_has_domain_paths():
    """P2-F2: DOMAIN_PATHS must not be defined in pipeline/pr_gates.py."""
    from pathlib import Path

    src = (
        Path(__file__).parent.parent.parent / "src/autoskillit/pipeline/pr_gates.py"
    ).read_text()
    assert "DOMAIN_PATHS" not in src


def test_pipeline_init_no_longer_exports_domain_paths():
    """P2-F2: DOMAIN_PATHS must not appear in pipeline.__all__."""
    import autoskillit.pipeline as m

    assert "DOMAIN_PATHS" not in m.__all__
    assert "partition_files_by_domain" not in m.__all__
