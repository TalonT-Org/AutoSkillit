"""IL-1/IL-2/IL-3 sub-package isolation, __all__ completeness, size/file-count constraints.

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

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

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
        "_fleet",  # cli/_fleet.py: fleet_app = App(...)
        "app",  # cli/app.py: app = App(...), config_app = App(...), etc.
        "store",  # migration/store.py: defensive exemption for future module-level construction
        "validator",  # recipe/validator.py: defensive exemption for decorator-based rule registry
        "settings",  # config/settings.py: _CONFIG_SCHEMA = _build_config_schema()
        "_headless_path_tokens",  # execution/_headless_path_tokens.py: _OUTPUT_PATH_TOKENS
        "_probe_cache",  # execution/backends/_probe_cache.py: PROBE_CACHE_TTL = timedelta(...)
        # Typed cancellation state is required for the authenticated broker boundary (#4585).
        "tools_evidence_reader",
        # _STAGING_ORPHAN_GRACE = timedelta(hours=1)
        "_generation_publication",
        # _STABLE_DISMISS_WINDOW = timedelta(days=7), _DEV_DISMISS_WINDOW = timedelta(hours=12)
        "_install_info",  # cli/install/_install_info.py: window constants (see comment above)
        # KITCHEN_GUARDED_COMMANDS: frozenset[str]
        "_update_checks",  # cli/_update_checks.py: module-level frozenset (see comment above)
        # _HTTP_TIMEOUT = httpx.Timeout(...) — module-level httpx client timeout config
        "_update_checks_fetch",  # cli/_update_checks_fetch.py: _HTTP_TIMEOUT constant
        "_terminal",  # cli/_terminal.py: _BASE_RESET = "".join(...) derived from _RESET_SPEC
        "_reconcile",  # hooks/_capture/_reconcile.py: immutable owner budget contracts
        "_capture_store",  # cli/ops/_capture_store.py: RECLAIM_BUDGET = SweepBudgetSpec(...)
        "hook_registry",  # hook_registry.py: HOOK_REGISTRY_HASH = compute_registry_hash(...)
        "_fleet",  # cli/_fleet.py: fleet_app = App(name="fleet", ...)
        "_features",  # cli/_features.py: features_app = App(name="features", ...)
        "_sessions",  # cli/ops/_sessions.py: sessions_app = App(name="sessions", ...)
        "_validate",  # cli/_validate.py: validate_app = App(name="validate", ...)
        "_type_backend",  # core/types/_type_backend.py: CLAUDE_CODE_CAPABILITIES constant
        "claude",  # execution/backends/claude.py: _ANNOTATION_SUPPORT_MIN = Version(...)
        "_prompts",  # cli/prompts/_prompts.py: immutable startup recovery spec and rendering
        "tools_fleet_dispatch",  # request-scoped fleet provenance ContextVars
        "_provenance",  # request-scoped fleet dispatch provenance ContextVars (submodule)
        "_run_skill_completion",  # request-scoped #4457 receipt delivery bindings
        # Released reducer definitions are immutable registry values keyed by their own
        # protocol version so the selector cannot drift from the registered definition.
        "context_admission",
        # Canonical output-discipline block/digest and their SHA-256 cache identity are
        # deliberately derived once at import time from the single source of truth.
        "_type_constants",
        # CODEX_INTAKE_DISCIPLINE_DIGEST is rendered once from CODEX_INTAKE_RULES at
        # import time, and its byte length is checked against the budget in the same
        # module-load self-check block (#4351).
        "_type_intake_policy",
        "_type_constants_registries",  # measured response-exemption registry digest
        "_type_dimensions",  # named conversion policies (BytesToTokensPolicy instances)
        "tool_registry",  # immutable canonical MCP tool definition registry
        # Frozen static ownership and identity-profile definitions are derived once.
        "_type_audit_admission",
        "_codex_config",  # Codex output ceiling derived from measured exemptions
        "_fmt_response_spill",  # standalone spill schema and exemption mirror digests
        "_response_budget",  # canonical spill schema digest
        "_primitives",  # server/_response_budget/_primitives.py: SHA-256 hexdigests
        # derived once at import time from the canonical spill schema
        "_explorer_projection",  # server-owned logger and immutable projection authority
        "_explorer_dispatch",  # immutable backend-specific native dispatch renderers
        "tools_recipe",  # request-scoped recipe pagination ContextVar (delegated)
        "_recipe_section_handler",  # request-scoped recipe pagination ContextVar
        # Thread-safe callback registry decouples artifact retirement from page-cache lifecycle.
        "_lifecycle",
        # _REMOVE_LABELS = sorted(...) — stable label list derived from LABEL_LIFECYCLE_REGISTRY
        "_label_cleanup",  # fleet/_label_cleanup.py: _REMOVE_LABELS constant (see comment above)
        "_step_context",  # core/_step_context.py: current_step_name, current_order_id ContextVars
        "_api_cache",  # recipe/_api_cache.py: _LOAD_CACHE = LoadCache()
        "_contracts_manifest",  # recipe/_contracts_manifest.py: _MANIFEST_CACHE = YamlFileCache()
        "skill_capabilities",  # workspace/skill_capabilities.py: bounded evidence cache
        "methodology_venue_appendix",  # recipe/methodology_venue_appendix.py: _ML_SUB_AREA_CACHE
        "rules_blocks",  # recipe/rules/rules_blocks.py: _BUDGETS_CACHE = YamlFileCache()
        "rules_phoropter_adjacency",  # recipe/rules/rules_phoropter_adjacency.py: _PREFIXES_CACHE
        # _RETENTION_SECONDS resolved once at import time from the single-source-of-truth
        # STATE_RECLAIMABILITY sweep grace (_lifecycle_policy.SWEEP_GRACE_SECONDS).
        "_capture_lifecycle",
        # join ledger alphabet/filename constants resolved once at import time.
        "_join_ledger",  # hooks/_join_ledger.py: _BATCH_ID_ALPHABET, LEDGER_FILENAME
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
        "cmd_keyword_pattern",  # recipe/_rule_helpers.py: regex factory returning compiled Pattern
        "object",
        "MappingProxyType",  # types.MappingProxyType — read-only view, no state
    }
)
_SINGLETON_SAFE_ASSIGNMENTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/autoskillit/core/types/_type_dimensions.py", "ASCII_YAML_POLICY"),
        ("src/autoskillit/hooks/_capture/_types.py", "TRANSITION_RESCUE_BUDGET"),
        ("src/autoskillit/pipeline/context_admission_ledger.py", "_EVENT_TYPES"),
        ("src/autoskillit/pipeline/context_admission_ledger.py", "_EFFECT_TYPES"),
        ("src/autoskillit/pipeline/context_admission_ledger.py", "_STATE_TYPES"),
        (
            "src/autoskillit/server/tools/tools_kitchen/_open_kitchen_transition.py",
            "_OPEN_KITCHEN_REQUEST_CTX",
        ),
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
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "sync_manifest" not in stripped, (
                    f"Found sync_manifest import in {py_file}: {line!r}"
                )


# ── Rule 2: test_singleton_definition_locality ────────────────────────────────


@pytest.mark.parametrize("source_file", _SOURCE_FILES)
def test_singleton_definition_locality(source_file: Path) -> None:
    """Module-level constructor calls are only permitted in SINGLETON_ALLOWED_MODULES."""
    mod_stem = source_file.stem
    source_path = _rel(source_file)
    if mod_stem in SINGLETON_ALLOWED_MODULES:
        pytest.skip(f"{mod_stem!r} is in SINGLETON_ALLOWED_MODULES")

    tree = ast.parse(source_file.read_text())
    violations: list[str] = []
    for node in tree.body:  # module-level only
        rhs: ast.expr | None = None
        target_name: str | None = None
        if isinstance(node, ast.Assign) and node.value:
            rhs = node.value
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and node.value:
            rhs = node.value
            if isinstance(node.target, ast.Name):
                target_name = node.target.id
        if rhs is None or not isinstance(rhs, ast.Call):
            continue
        if (
            target_name is not None
            and (
                source_path,
                target_name,
            )
            in _SINGLETON_SAFE_ASSIGNMENTS
        ):
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


def test_context_admission_ledger_singletons_are_assignment_scoped() -> None:
    assert "context_admission_ledger" not in SINGLETON_ALLOWED_MODULES
    assert {
        target
        for path, target in _SINGLETON_SAFE_ASSIGNMENTS
        if path == "src/autoskillit/pipeline/context_admission_ledger.py"
    } == {"_EVENT_TYPES", "_EFFECT_TYPES", "_STATE_TYPES"}


def test_capture_types_singleton_is_path_and_assignment_scoped(tmp_path: Path) -> None:
    assert "_types" not in SINGLETON_ALLOWED_MODULES
    assert (
        "src/autoskillit/hooks/_capture/_types.py",
        "TRANSITION_RESCUE_BUDGET",
    ) in _SINGLETON_SAFE_ASSIGNMENTS
    unrelated = tmp_path / "_types.py"
    unrelated.write_text("TRANSITION_RESCUE_BUDGET = SweepBudgetSpec()\n")

    with pytest.raises(AssertionError, match="Singleton locality violations"):
        test_singleton_definition_locality(unrelated)


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
    """Severity must be a top-level class in core/types/_type_enums.py (the enums sub-module)."""
    tree = _get_module_ast("core/types/_type_enums.py")
    assert "Severity" in _top_level_class_names(tree), (
        "Severity not found in core/types/_type_enums.py; it must be defined there"
    )


def test_skill_tools_defined_in_types():
    """SKILL_TOOLS must be a top-level assignment in _type_constants_registries.py."""
    tree = _get_module_ast("core/types/_type_constants_registries.py")
    assert "SKILL_TOOLS" in _top_level_assign_targets(tree), (
        "SKILL_TOOLS not found in core/types/_type_constants_registries.py;"
        " it must be defined there"
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
    ast_module = _get_module_ast("cli/doctor/__init__.py")
    class_names = _top_level_class_names(ast_module)
    assert "Severity" not in class_names, (
        "cli/doctor/__init__.py must import Severity from autoskillit.core, not define it locally"
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
    ast_module = _get_module_ast("recipe/_contracts_types.py")
    assigns = _top_level_assign_targets(ast_module)
    assert "_CONTEXT_REF_RE" in assigns, "recipe/_contracts_types.py must define _CONTEXT_REF_RE"
    assert "INPUT_REF_RE" in assigns, "recipe/_contracts_types.py must define INPUT_REF_RE"


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


# ── New IL-2 sub-package tests (T1–T7 from groupC plan) ─────────────────────────


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
        validate_recipe_cards,
        validate_recipe_structure,
    )


def test_contracts_module_has_staleitem() -> None:
    """T2: recipe/contracts.py exposes StaleItem and load_bundled_manifest."""
    from autoskillit.recipe.contracts import StaleItem, load_bundled_manifest  # noqa: F401


def test_validator_module_has_validate() -> None:
    """T3: validator.py exposes validate_recipe_structure + run_semantic_rules."""
    from autoskillit.recipe.validator import (  # noqa: F401
        analyze_dataflow,
        run_semantic_rules,
        validate_recipe_structure,
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


# ── New IL-3 package tests (groupD plan) ────────────────────────────────────────


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
    """server/ must not exceed 18 Python files (REQ-DSGN-002).

    Limit updated from 14 to 16 after tools_integrations was split into
    tools_github, tools_issue_lifecycle, and tools_pr_ops.
    Limit updated from 16 to 17 after _editable_guard.py was added as
    the pre-deletion editable install guard for perform_merge().
    Limit updated from 17 to 18 after _lifespan/_lifespan.py was added for
    FastMCP server lifespan teardown (#745).
    Limit updated from 18 to 19 after _wire_compat.py was added for
    Claude Code wire-format sanitization middleware.
    Limit updated from 19 to 20 after _session_type.py was added for
    session-type tag visibility dispatch (3-branch startup logic).
    Limit updated from 20 to 22 after tools_ci.py was split into
    tools_ci_watch.py and tools_ci_merge_queue.py submodules.
    Limit updated from 22 to 23 after _guards.py was extracted from helpers.py.
    Limit updated from 23 to 24 after _subprocess.py was extracted from helpers.py.
    Limit updated from 24 to 25 after _misc.py was extracted from helpers.py.
    Limit updated from 25 to 28 after #4557 decomposed _recipe_delivery.py
    into _recipe_artifact.py + _recipe_delivery_helpers.py + _recipe_delivery.py,
    and _recipe_section_pagination.py into _recipe_section_planning.py +
    _recipe_section_pagination.py.
    """
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= 28, f"server/ has {len(py_files)} files, max is 28"


def test_tools_integrations_replaced_by_split_modules() -> None:
    """tools_integrations.py deleted; four replacement modules exist."""
    server = SRC_ROOT / "server"
    assert not (server / "tools_integrations.py").exists()
    assert not (server / "tools" / "tools_issue_lifecycle.py").exists()
    assert (server / "tools" / "tools_github.py").exists()
    assert (server / "tools" / "tools_issue_headless.py").exists()
    assert (server / "tools" / "tools_issue_labels.py").exists()
    assert (server / "tools" / "tools_pr_ops.py").exists()


def test_split_files_under_750_lines() -> None:
    """Each split module must stay under the 750-line threshold."""
    server = SRC_ROOT / "server"
    for name in (
        "tools_github.py",
        "tools_issue_headless.py",
        "tools_issue_labels.py",
        "tools_pr_ops.py",
    ):
        lines = len((server / "tools" / name).read_text().splitlines())
        assert lines <= 750, f"{name} has {lines} lines, exceeds 750"


def test_extract_block_in_misc() -> None:
    """_extract_block lives in server/_misc.py."""
    from autoskillit.server._misc import _extract_block

    assert callable(_extract_block)


def test_all_tools_importable_from_split_modules() -> None:
    """All 8 tools are importable from their new home modules."""
    from autoskillit.server.tools.tools_github import (
        fetch_github_issue,
        get_issue_title,
        report_bug,
    )
    from autoskillit.server.tools.tools_issue_headless import prepare_issue
    from autoskillit.server.tools.tools_issue_labels import (
        claim_issue,
        release_issue,
    )
    from autoskillit.server.tools.tools_pr_ops import bulk_close_issues, get_pr_reviews

    for name, fn in [
        ("fetch_github_issue", fetch_github_issue),
        ("get_issue_title", get_issue_title),
        ("report_bug", report_bug),
        ("prepare_issue", prepare_issue),
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
    assert (SRC_ROOT / "cli" / "doctor" / "__init__.py").exists()


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
    """No test file at tests/ root exceeds 1,000 lines after groupE split.

    Exemptions (rule ID | rationale):
      test_smoke_utils.py — REQ-CNST-004-E1: Contains 13 callable-unders tests,
        all using tmp_path isolation and dict[str,str] assertions. No shared state.
        Splitting would scatter the T_* pattern across files, reducing discoverability.
        Exempt at 1348 lines.
    """
    tests_root = Path(__file__).parent.parent
    over = [
        f"{f.name} ({len(f.read_text().splitlines())} lines)"
        for f in tests_root.glob("test_*.py")
        if len(f.read_text().splitlines()) > 1000
        and f.name != "test_smoke_utils.py"  # REQ-CNST-004-E1
    ]
    assert not over, f"Oversized test files remain (run groupE): {over}"


def test_no_subpackage_exceeds_10_files() -> None:
    """REQ-CNST-003: No sub-package directory may contain more than 10 Python files.

        Exemptions (rule ID | rationale):
          server/ — REQ-CNST-003-E1: server/ splits tool handlers into per-domain files
            (tools_clone, tools_github, tools_issue_headless, tools_issue_labels, tools_pr_ops,
            tools_ci, tools_git, tools_recipe, tools_status, tools_workspace, tools_execution,
            tools_kitchen, helpers, git, _factory, _state, __init__); each file is a thin
            routing layer. Exempt at 16 files.
            _progress_heartbeat.py adds the MCP progress-notification context manager,
            bringing the count to 28.
          recipe/ — REQ-CNST-003-E2: recipe/ hosts one file per semantic-rule domain
            (rules_bypass, rules_ci, rules_clone, rules_packs, etc.) for independent testability.
            Adding rules_cmd.py for run_cmd echo-capture alignment validation and
            rules_isolation.py for workspace isolation checks brings the count to 30.
            rules_blocks.py adds the block-level budget rule family, bringing the count to 32.
            rules_reachability.py adds symbolic BFS reachability rules, bringing the count to 33.
            rules_fixing.py adds conditional-write-skill ungated-push detection,
            bringing the count to 34.
            rules_campaign_dispatch.py, rules_campaign_deps.py, rules_campaign_ingredients.py,
            rules_campaign_capture.py, and rules_campaign_flow.py split rules_campaign.py,
            bringing the count to 37.
            rules_temp_path.py adds the non-unique-output-path lint rule for output path
            isolation enforcement, bringing the count to 39.
            identity.py adds recipe identity hashing (content and composite fingerprints),
            bringing the count to 40.
            order.py adds the stable display order registry (BUNDLED_RECIPE_ORDER) for
            Group 0 bundled recipes, bringing the count to 41.
            Monolithic file splits (_api.py → _recipe_ingredients + _recipe_composition;
            _analysis.py → _analysis_graph + _analysis_bfs + _analysis_blocks +
            _analysis_detectors) add 6 files, bringing the count to 47.
            _skill_helpers.py extracts the shared _get_skill_category_map helper from
            rules_skills.py and rules_features.py to eliminate duplication,
            bringing the count to 48.
            rules/rules_callable_scope.py adds the callable-requires-scoped-discovery
            rule enforcing scoped directory arguments for file-discovering callables,
            bringing the rules/ count to 29. rules/rules_remediation.py adds the
            audit-impl-remediation-route rule ensuring remediation_path captures have
            non-terminal non-GO routes, bringing the rules/ count to 30.
            rules/rules_loop_progress.py adds the loop-body-uncaptured-output rule
            ensuring run_skill steps inside routing cycles capture declared outputs,
            bringing the rules/ count to 31.
            rules_phoropter_adjacency.py adds phoropter phase-order and step-interleaving
            semantic validation rules, bringing the count to 50.
            rules_loop_counter.py adds loop-counter-cross-path-sharing and
            loop-guard-before-verify semantic rules, bringing the count to 51.
            Decomposition into campaign/, ci/, dataflow/, graph/ subdirectories moved
            files out of rules/, bringing the rules/ count to 35.
            rules_stamp_ownership.py adds the exclusive-stamp-ownership enforcement
            rule, bringing the rules/ count to 36.
            rules_gitignored_deliverable.py adds the gitignored-deliverable-in-plan
            rule flagging plan steps writing to gitignored paths that feed audit-impl,
            bringing the rules/ count to 37.
            rules_contract_recovery.py adds the contract-recovery-requires-salvage-route
            ERROR rule deriving on_context_limit salvage-route requirements from skill
            contract capability (#4305 part C), bringing the rules/ count to 38.
            Exempt at 51 files.
          execution/ — REQ-CNST-003-E3: execution/ decomposes process lifecycle into
            focused single-concern modules (_process_io, _process_kill, _process_race,
            etc.) that cannot be merged without re-introducing the coupling they isolate.
            recording.py adds the RecordingSubprocessRunner decorator as a separate module
            to keep scenario recording concerns isolated from the core process lifecycle.
            _headless_recovery.py owns both result recovery and write-path JSONL scanning.
            _headless_recovery.py, _headless_path_tokens.py, and _headless_result.py
            split the remaining headless.py concern groups into private sub-modules
            following the _process_*.py precedent (P8-F1), bringing the count to 29.
            _session_model.py and _session_content.py split session.py (P8-F3),
            _merge_queue_classifier.py and _merge_queue_repo_state.py split merge_queue.py
            (P8-F4), bringing the count to 33.
            _retry_fsm.py and _session_outcome.py split session retry and outcome logic,
            bringing the count to 35.
            _merge_queue_group_ci.py extracts merge-group CI helpers and GraphQL mutation/query
            strings from merge_queue.py to satisfy the 500-line size budget (P8-F4 follow-up),
            bringing the count to 36.
            _headless_git.py extracts git LOC-capture helpers (_capture_git_head_sha,
            _parse_numstat, _compute_loc_changed) from headless.py to keep it under the
            750-line architectural budget, bringing the count to 37.
            _recording_skills.py adds snapshot/restore helpers for ephemeral skill dirs in
            the record/replay system, isolated from recording.py to keep snapshot logic
            independently testable, bringing the count to 38.
            Exempt at 38 files.
          core/ — REQ-CNST-003-E4: core/ types split into per-concern type modules
            (_type_enums, _type_protocols_logging, _type_protocols_execution,
            _type_protocols_github, _type_protocols_workspace, _type_protocols_recipe,
            _type_protocols_infra, _type_results, _type_subprocess, etc.) to
            prevent circular imports while keeping IL-0 types co-located. Also houses
            _terminal_table.py as the IL-0 shared terminal rendering primitive so that
            both cli/ (IL-3) and pipeline/ (IL-1) can import it without layer violations.
            _claude_env.py adds the canonical IDE-scrubbing env builder for all
            claude subprocess launches. kitchen_state.py adds the stdlib-only
            kitchen-open session marker reader for hook subprocesses.
            _version_snapshot.py adds the process-scoped version snapshot for session
            telemetry (collect_version_snapshot, lru_cache'd).
            _plugin_cache.py adds the plugin cache lifecycle: retiring cache sweep,
            install locking, and kitchen registry (accessible from server/ without
            cli/ import).
            _plugin_artifact_identity.py isolates exact installed-artifact manifest
            validation from retirement-cache orchestration so both IL-0 authorities
            remain below the source-module line limit.
            feature_flags.py adds the IL-0 is_feature_enabled() primitive — must live
            in core/ to be importable by all layers without cross-layer violations.
            session_registry.py adds the stdlib-only session registry mapping
            autoskillit launch IDs to Claude Code session UUIDs for the scoped
            resume picker.
            tool_sequence_analysis.py adds the stdlib-only cross-session tool call
            sequence DFG analysis (IL-0; must live in core/ to be importable by server/).
            Monolithic protocol module split into 6 domain-grouped shard files (net +5 files).
            _install_detect.py adds the is_dev_install() predicate for config resolution
            to auto-detect whether the install is editable when experimental_enabled is absent,
            bringing the count to 33.
            _type_session_env.py adds FleetSessionEnv frozen dataclass for typed env spec
            at the session launch boundary, bringing the count to 20.
            _type_backend.py adds BackendCapabilities frozen dataclass and CLAUDE_CODE_CAPABILITIES
            constant for backend capability declarations (IL-0), bringing the count to 21.
            _type_token.py adds CanonicalTokenUsage frozen dataclass for provider-agnostic
            token usage normalization (IL-0), bringing the count to 22.
            _type_exceptions.py adds RecipeLoadError hierarchy (ProcessStaleError,
            RecipeNotFoundError) for exception-based error propagation from
            load_and_validate, bringing the count to 23.
            _type_phoropter.py adds frozen phoropter family/phase types
            (PhoropterPrescription, ReadingToken, PhoropterPhaseSkip,
            CrossDomainPrescription, CrossDomainAssessment) for the phoropter
            registry system, bringing the core/types count to 29.
            _type_tradition_manifest.py adds TraditionManifest, LensEntry,
            DialingConfig frozen dataclasses with from_yaml_path YAML loader
            for the tradition manifest system, bringing the core/types count to 30.
            _type_invariant_registry.py adds InvariantDef frozen dataclass and
            INVARIANT_REGISTRY mapping 13 prose prohibitions to runtime gate targets,
            bringing the core/types count to 31.
            _type_recipe_sections.py adds recipe-section schema and digest contracts.
            _type_skill_contract.py adds the backend-neutral SkillSourceRef identity
            consumed by workspace projections.
            _context_admission.py adds the pure context-admission reducer, and
            _type_context_admission.py adds its frozen IL-0 contract records.
            Exempt at 26 files (core/types: 36).
          cli/ — REQ-CNST-003-E5: cli/ retains _terminal_table.py as a re-export shim
            for backward-compatible cli/ imports; canonical implementation lives in
            core/_terminal_table.py. Also contains _terminal.py — the terminal state
            management context manager (terminal_guard) for interactive subprocess
            sessions. _update_checks.py adds the unified update check orchestration.
            _update.py adds the first-class update subcommand implementation.
            _fleet.py adds fleet error envelope rendering for CLI consumers.
            _features.py adds feature gate inspection subcommand (list/status).
            _session_picker.py adds the scoped session resume picker that filters
            sessions by type (cook/order) using the session registry.
            _doctor.py was split (1245 lines → facade + 9 sub-modules) following the
            _process_*.py pattern: _doctor_types.py (shared DoctorResult type),
            _doctor_mcp.py, _doctor_hooks.py, _doctor_install.py, _doctor_config.py,
            _doctor_runtime.py, _doctor_env.py, _doctor_features.py, _doctor_fleet.py.
            The CLI is organized as: `cli/prompts/` (prompt builders — _prompts,
            _prompts_campaign, _prompts_kitchen, _prompts_orchestrator),
            `cli/install/` (install cluster — _install_contract, _install_info,
            _installed_plugins, _marketplace, _plugin_artifact), `cli/ops/`
            (diagnostic subcommand runners — _capture_store, _codex_attempts,
            _codex_orphans, _daemon_orphans, _process_orphans, _sessions),
            `cli/session/` (cook/order lifecycle — _session_cook, _session_order,
            _session_onboarding, _session_launch, _session_backend,
            _session_constants, _session_picker, _session_process,
            _session_reload, _session_startup_trace), `cli/update/` (update
            pipeline — _update, _update_checks, _update_checks_source,
            _update_checks_fetch, _transaction, _obligation_repair, _restart),
            and `cli/doctor/` (doctor commands — _doctor_types, _doctor_mcp,
            _doctor_hooks, _doctor_install, _doctor_config, _doctor_runtime,
            _doctor_env, _doctor_features, _doctor_fleet, _doctor_skills,
            _doctor_capture_store, plus the facade).
            The 11 remaining top-level files (app.py + 10 small shared utilities —
            see the dict entry below) are the orchestration entry points and shared
            helpers that have no coherent subpackage home.
            _hooks_codex.py adds Codex config.toml hook generation and sync
    (generate_codex_hooks_config, sync_hooks_to_codex_config) paralleling
    _hooks.py for Claude Code settings.json hooks.
    Exempt at 11 files.
          hooks/ — REQ-CNST-003-E6: hooks/ hosts one standalone script per hook event
            (PreToolUse, PostToolUse, SessionStart). Each script must remain a separate
            file so Claude Code can invoke it directly as a subprocess. pretty_output_hook.py
            additionally owns a set of underscore-prefixed private formatter modules
            (_fmt_primitives.py, _fmt_execution.py, _fmt_status.py, _fmt_recipe.py)
            that are imported helpers — not standalone hook scripts — split out to
            keep pretty_output_hook.py under its line budget. ask_user_question_guard.py
            gates AskUserQuestion on kitchen-open state. grep_pattern_lint_guard.py adds
            input-validation guard for Grep tool BRE pattern syntax. review_gate_post_hook.py
            and review_loop_gate.py add the review gate enforcement hooks. recipe_write_advisor.py
            adds a non-blocking advisory hook for recipe YAML writes. write_guard.py
            blocks Write/Edit outside the allowed prefix in read-only skill sessions.
            _hook_utils.py provides shared stdlib-only utilities (e.g., find_project_root)
            for hook scripts that need common path resolution logic.
            _command_classification.py adds shared command classification primitives
            (interpreter/wrapper detection) for all command-classifying guards.
            quota_guard_state_post_hook.py is a stdlib-only PostToolUse script that
            maintains the per-session quota-disable marker. Exempt at 15 files.
            output_budget_guard.py was deleted and retired; its enforcement moved to
            shell_capture_hook.py (input-rewrite mechanism) at the hooks/ package root.
            Exempt at 32 files.
          pipeline/ — REQ-CNST-003-E7: pipeline/ added github_api_log.py for session-scoped
            GitHub API request tracking (DefaultGitHubApiLog accumulator + GitHubApiEntry).
            context_admission_ledger.py adds crash-safe shadow accounting, and
            recipe_initialization.py adds the pure named-recipe lifecycle reducer.
            Exempt at 14 files.
          fleet/ — REQ-CNST-003-E8: fleet/ added _semaphore.py for FleetSemaphore, the
            configurable asyncio.BoundedSemaphore implementation of the FleetLock protocol.
            Placed in fleet/ rather than server/ to preserve conservative test-filter cascade
            narrowing: changes to fleet/_semaphore.py only cascade to fleet/ tests, not to
            server/ tests. state.py was decomposed into state_types.py, state_gates.py, and
            state_recovery.py to reduce the 757-line monolith and centralize deserialization
            logic on DispatchRecord.from_dict. Exempt at 15 files.
    """
    EXEMPTIONS: dict[str, int] = {
        # +generation-bound replay store and post-enforcement initialization commits.
        "server": 28,  # +_progress_heartbeat MCP idle-abort immunity boundary
        # +_recipe_segment_delivery plan-mandated progressive delivery boundary
        # +_recipe_artifact.py (persistence), +_recipe_delivery_helpers.py (attestation,
        # margins, manifest planning), +_recipe_section_planning.py (page-fitting engine)
        # — #4557 decomposes three modules over the 750-line structural limit
        # +_recipe_raw_repair: cohesive raw-YAML repair responsibility (#4553).
        "recipe": 43,  # was 33; +9 from CI/graph/dataflow splits
        # +_github_http review boundary and +launch_resolution authority.
        "execution": 21,  # +session_index strict byte-bounded retained-index reads (#4514)
        # +evidence_reader sterile reader lifecycle (#4585)
        # +agent_definition native-role authority (#4443).
        "core": 32,  # +pipeline_tracker: shared IL-0 tracker authority and leases (#4293)
        # +GitHub review types, portable launch authority, stable contract,
        # closed skill semantics, non-executable projection binding, explorer contracts,
        # execution-identity value objects/protocols, and the typed maintenance-install
        # subprocess boundary, and dimension-safe recipe delivery limits.
        "core/types": 53,
        "cli": 11,  # issue #4670 Part B final state: 11 top-level files remain
        # (app.py + 10 small shared utilities — _features.py, _hooks.py,
        # _hooks_codex.py, _init_helpers.py, _mcp_names.py, _preview.py,
        # _serve_guard.py, _validate.py, _workspace.py, __init__.py); no
        # coherent subpackage home exists for any of them
        "cli/session": 11,  # +_session_onboarding.py folded in from cli/_onboarding.py,
        # first-run detection consumed only by _session_cook.py (#4670)
        "cli/doctor": 12,  # +_doctor_skills capability declaration authenticity checks;
        # +_doctor_capture_store read-only capture-store stats check
        "workspace": 16,  # +_installed_artifact exact lease-protected authority (#4409);
        # +_install_state (single install-state consistency authority,
        # replacing nine ad-hoc repairs) +_projection_cache (asset inventory, cache-key
        # record, and orphan sweep — split out so staleness cannot drift from projection)
        # +_update_obligation (persisted "republication owed" journal; must be writable
        # by cli/update/ and readable by server/_lifespan/_startup_checks.py without a
        # server->cli edge, so it lives at this IL-1 layer rather than splitting further —
        # its 176 lines are one cohesive read/write/clear API with no internal seam to extract)
        "hooks": 25,  # +_capture_process owned shell process-group boundary;
        # +_hook_payload shared payload parser for guards  # noqa: E501
        # +context/audit admission ledgers, recipe initialization, exploration lifecycle,
        # and request-correlated exploration identity records
        # +_github_mutation_analysis (#4665) — see _LINE_LIMIT_EXEMPTIONS below
        # Bumped 24 -> 25: CI reported 25 Python files at SHA 869746ddc
        # (24 in the local git-tracked file set), so the cap must tolerate
        # whatever CI-side enumeration produced the +1 difference.
        "pipeline": 19,  # +run_skill_completion server-owned receipt authority (#4457)
        # +kitchen transition authority
        # +exploration_context_durable.py: durable (0600 HMAC-signed) session-scoped
        # exploration authority, split from exploration_context.py to stay under its
        # own REQ-CNST-010-E22 1100-line ceiling (#4684 Fix E)
        "fleet": 23,  # +_issue_url_helpers.py  # noqa: E501
        "recipe/rules": 57,  # +commit_guard_regression_route +rules_model +rules_gitignored_deliverable +rules_issue_scope_threading +rules_inventory_gate_bilateral +rules_verdict_context +rules_contract_recovery +rules_audit_outcome_routing +rules_note_shape_contradiction  # noqa: E501
        "server/tools": 39,  # noqa: E501 # +tools_exploration read-only broker endpoints; +tools_session_logs bounded retained-log reader (#4514); +tools_evidence_reader fail-closed behavioral evidence surface +_evidence_reader deep feedback authority (#4585); +_pipeline_deps.py +_ordering_telemetry.py (open_kitchen
        # auto-init dependency tracker + REVIEW_BEFORE_PLAN ordering telemetry)
        # +_backend_compat.py (shared target-resolution + fail-closed compatibility gate
        # for direct headless executor callers — report_bug, prepare_issue)
        # +tools_audit_artifacts.py (typed audit semantic/disposition producers, #4419;
        # replaces the retired generic audit-cycle writer)
        # +_overlay_state.py (single locked, validated session-overlay boundary)
        # +_recipe_section_handler.py (bounded recipe-section pull handler)
        "hooks/guards": 40,  # +github_mutation_guard (#4432); +4 join_*_guard (#4575)
        # +resource_exhaustion_guard (#4678 rectify: Bash busy-loop pattern denial)
        # +fabricated_completion_guard (#4457)
        "execution/process": 11,  # +_termination (RE: #4664 decompose); +_process_tether
        # (#4678 rectify: process-tether spawner-death immunity mechanism)
        # +exploration_request_identity_guard request-correlated Claude authority (#4512)
        # Three private Codex ownership modules keep lock, prelaunch transaction,
        # and per-attempt storage concerns out of the public backend gateway:
        # +_codex_config_lock, +_codex_prelaunch, +_codex_session_storage.
        # +_explorer_conformance version-bound live attestation authority (#4443)
        "execution/backends": 30,  # +decomposed _codex_* and _claude_* siblings (#4664)
        "execution/github_review": 15,  # +_ledger_schema and _poster_post_attempt siblings (#4664)
        "execution/headless": 15,  # +_headless_adjudication from _headless_result (#4664)
        "execution/session": 20,  # +codec and lineage siblings decomposed out (#4664)
        # +_codex_catalog shared validated catalog projection (#4585)
        "smoke_utils": 12,  # +_review_design split from _review
    }
    violations: list[str] = []
    dirs_to_check: list[Path] = []
    for sub_dir in sorted(SRC_ROOT.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_") or sub_dir.name == "__pycache__":
            continue
        dirs_to_check.append(sub_dir)
        for nested_dir in sorted(sub_dir.iterdir()):
            if (
                not nested_dir.is_dir()
                or nested_dir.name.startswith("_")
                or nested_dir.name == "__pycache__"
            ):
                continue
            dirs_to_check.append(nested_dir)
    for sub_dir in dirs_to_check:
        rel_key = str(sub_dir.relative_to(SRC_ROOT))
        py_files = list(sub_dir.glob("*.py"))
        limit = EXEMPTIONS.get(rel_key, 10)
        if len(py_files) > limit:
            violations.append(f"{rel_key}/: {len(py_files)} Python files (max {limit})")
    assert not violations, "Sub-packages exceeding 10 Python files:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_data_directories_are_not_python_packages() -> None:
    """REQ-ARCH-005: data-only directories under src/autoskillit/ must not
    contain __init__.py — that turns them into phantom Python packages
    distinct from the real IL-2 module of similar name."""
    src = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    data_dirs = {"migrations", "recipes", "skills", "skills_extended", "agents"}
    offenders: list[str] = []
    for name in data_dirs:
        d = src / name
        if not d.is_dir():
            continue
        init = d / "__init__.py"
        if init.exists():
            offenders.append(str(init.relative_to(src)))
    assert not offenders, (
        f"Data directories must not be Python packages. Remove __init__.py from: {offenders}"
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
    "core/_plugin_cache.py": (
        1100,
        "REQ-CNST-010-E26: #4689 added try_promote_legacy_evidence beside try_reclaim. "
        "Both mutate the retiring cache under the install lock and must stay adjacent to "
        "the append/remove/read primitives they call, for the same reason "
        "_projected_artifact/AGENTS.md keeps publication beside lease handoff: splitting "
        "them puts lock ordering across a module boundary, which is how destructive "
        "repair bypasses the lifecycle lock. tests/infra/test_plugin_source_ratchets.py "
        "also pins this module's raw-mutation call sites by (file, function, expression), "
        "so the reclaim path's location is a checked invariant, not an accident.",
    ),
    "execution/evidence_reader.py": (
        1500,
        "REQ-CNST-010-E25: #4585 keeps sterile auth, projection, probes, managed process "
        "lifecycle, and strict result validation behind one evidence-reader launch interface",
    ),
    "execution/process/__init__.py": (
        1050,
        "REQ-CNST-010-E27: #4678 rectify threads ceiling_seconds through run_managed_async/"
        "run_managed_sync/DefaultSubprocessRunner and adds the PTY-wrapper workload-identity "
        "resolution for the process-tether spawner-death immunity mechanism — this facade is "
        "the single composition point for both spawn paths and must stay adjacent to the "
        "spawn call sites it wires the tether into.",
    ),
    # REQ-CNST-010-E1: core/types.py is the canonical type registry for the entire
    # package. It defines all StrEnums, protocols, constants, and shared type aliases
    # in one place to prevent circular imports across sub-packages. Exempt at 1200 lines.
    "types.py": (
        1200,
        "REQ-CNST-010-E1: canonical type registry — wide surface required to prevent "
        "circular imports; all enums/protocols/constants consolidated here",
    ),
    "recipe/_binding.py": (
        1050,
        "REQ-CNST-010-E24: #4402 keeps runtime attestation admission and its "
        "parameter-role denial remedies beside the compile-time binding pipeline",
    ),
    "hooks/_capture_artifacts.py": (
        1200,
        "REQ-CNST-010-E22: descriptor-anchored capture authority and isolated runner — "
        "re-exports capture_store_stats, reconcile_capture_store, CaptureStoreStats, "
        "CleanupBlocker, CleanupProgress, and SweepBudgetSpec from its own dual-mode "
        "(flat sys.path / dotted package) _capture import bootstrap so hooks/__init__.py "
        "can gateway them to cli/ops/_capture_store.py without importing _capture submodules "
        "directly, which would race the standalone hook scripts' own flat-style bootstrap "
        "of sys.modules['_capture']. Bumped for ADR-0009's failure-disposition routing "
        "(bookkeeping vs. integrity) and the capacity injection seam (issue #4479).",
    ),
    "hooks/_capture_lifecycle.py": (
        1250,
        "REQ-CNST-010-E21: capture lifecycle store — the lock-retry primitive "
        "(_acquire_flock, jittered exponential backoff bounded by the active sweep's "
        "own budget) and directory-reconciliation orphan admission "
        "(_admission_reason, _admit_new_record, _scan_and_adopt_orphans) must stay "
        "adjacent to the transition/capacity accounting they share; splitting would "
        "separate self-accounting invariants from the store methods that enforce them. "
        "Bumped for ADR-0009's rescue-sweep-and-retry pressure immunity at both the "
        "admission and transition gates (issue #4479).",
    ),
    "hooks/_capture_contract.py": (
        1100,
        "REQ-CNST-010-E23: CaptureFailureV3 envelope framing — carries the full "
        "CaptureFailureReason wire vocabulary and its (V2 marker) rendering; ADR-0009 "
        "added the SNAPSHOT_INTEGRITY reason and degraded-delivery envelope fields, "
        "which must stay co-located with the rest of the envelope schema they extend "
        "(issue #4479).",
    ),
    "hooks/_command_classification.py": (
        1600,
        "REQ-CNST-010-E10: shared command-classification primitive consumed by all "
        "command-inspecting guards — tokenization, shell-payload extraction, "
        "interpreter-write detection, protected-path reads, and recursive payload "
        "segmentation; the stdlib-only hook boundary and shared parser prevent "
        "policy drift across guard processes. Cap reduced to 1300 by #4665's "
        "decomposition of GitHub mutation cardinality/route authority into the "
        "_github_mutation_analysis.py sibling under E26. Bumped to 1600 for Issue "
        "#4655's rectify: ArgvToken threads quote provenance through the tokenizer "
        "(_tokenize_command_segments_with_redirects, _partition_output_redirect_"
        "indices/_select_executable_argv_tokens, _verb_start_index), and the CLI-"
        "agnostic _FlagArity/_consume_argv_flag/_consume_str_flag spec-table engine "
        "(plus _GIT_GLOBAL_FLAG_SPEC and _PIP_GLOBAL_FLAG_SPEC, and "
        "extract_git_subcommand_and_flags's fail-closed unrecognized-global-flag fix) "
        "-- these are shared, CLI-agnostic primitives every command-inspecting guard "
        "consumes (git, curl, pip, and gh's own spec table in "
        "_github_mutation_analysis.py, which imports this engine rather than "
        "duplicating it), so they stay adjacent to the tokenizer they extend rather "
        "than the gh-specific consumer module the split already separated them from.",
    ),
    "hooks/_github_mutation_analysis.py": (
        1600,
        "REQ-CNST-010-E26: #4665 decomposes the GitHub mutation cardinality/route "
        "analysis out of _command_classification.py into this sibling module — the "
        "gh/curl possible-exec token check, gh issue edit's target/flag grammar, "
        "statically proven fan-out count, gh mutation subcommand classification, and "
        "the recursive cardinality aggregator all share the same mutation authority "
        "and must stay adjacent to one another for test inspection (test_command_"
        "classification.py::TestAnalyzeGitHubMutations). Cap set to 1300 to bound "
        "the shared mutation authority after decomposition. Bumped to 1600 for Issue "
        "#4655's rectify: _GH_API_FLAG_SPEC and _CURL_FLAG_SPEC (this module's own "
        "gh-api/curl flag tables, consuming _command_classification's shared "
        "_FlagArity/_consume_argv_flag engine via a module-scope import) replace "
        "_analyze_gh_api/_analyze_curl_segment's ad-hoc if/elif flag chains so an "
        "unrecognized flag fails closed with a distinguishable reason code instead of "
        "being silently misparsed as a second route; and ArgvToken-typed "
        "_flag_value/_analyze_gh_api/_analyze_curl_segment/_is_dynamic_shell_value/"
        "_is_static_issue_edit_target/_issue_edit_request_count prove GraphQL "
        "documents and flag values shell-inert from quote provenance rather than "
        "content alone -- must stay adjacent to the mutation authority they feed.",
    ),
    "hooks/guards/git_ops_guard.py": (
        1050,
        "REQ-CNST-010-E28: Issue #4655's rectify moves this guard's checked-out-ref "
        "dynamic-value check onto the shared _DYNAMIC_SHELL_TOKEN_RE regex, which "
        "#4665's decomposition relocated to hooks/_github_mutation_analysis.py -- a "
        "second module-scope import block (alongside the existing "
        "_command_classification one) is needed since the two symbols now live in "
        "different sibling modules. Cap bumped from the 1000-line default to give "
        "this guard's own destructive-op/fetch/checked-out-ref classification room "
        "without re-tripping the limit on the next small addition.",
    ),
    "session.py": (
        1060,
        "REQ-CNST-010-E3: session adjudication pipeline — exhaustive match arms "
        "for TerminationReason require explicit IDLE_STALL arms in _compute_success, "
        "_compute_retry, and ClaudeSessionResult.normalize_subtype; "
        "lifespan_started heuristic added",
    ),
    "_doctor.py": (
        1300,
        "REQ-CNST-010-E4: doctor check registry — 28 sequential checks require inline logic; "
        "splitting into sub-modules would obscure the check sequence and break the test "
        "filter cascade",
    ),
    "skills.py": (
        1350,
        "REQ-CNST-010-E14: skill resolution + sidecar parsing — exploration.yaml sidecar "
        "loader and parser are tightly coupled to _skill_info_from_frontmatter and the "
        "marker binder; extracting them would create an artificial module boundary while "
        "the sidecar is read exactly once in the same parse event as SKILL.md frontmatter",
    ),
    "fleet/_api.py": (
        1590,
        "REQ-CNST-010-E6: fleet dispatch engine — evaluate_skip_when inlined here to avoid "
        "a 16th fleet/ module (sub-package file ceiling); keeps dispatch-related helpers "
        "co-located with the execution engine that calls them. Bumped to 1200 by the "
        "fleet-resume-precondition-chokepoint plan: prepare_resume chokepoint, "
        "closure-scoped _spawn_error, and _write_pid fail-closed contract add ~33 lines. "
        "Bumped to 1550 for #4417's per-effect dispatch provenance checkpoints and "
        "post-start crash persistence; those checkpoints must remain adjacent to the "
        "side effects whose ambiguity they record. Bumped to 1575 so the managed native "
        "shell lineage decision and provenance snapshot remain on the same dispatch "
        "transaction boundary after conflict resolution. Bumped to 1590 for shared "
        "tracker-authority retention and cleanup on every dispatch outcome boundary.",
    ),
    "server/_recipe_delivery.py": (
        750,
        "REQ-CNST-010-E12: #4557 decomposes recipe delivery into _recipe_delivery.py "
        "(finalization orchestrator) and _recipe_artifact.py (persistence, attestation, "
        "helper types).",
    ),
    "server/_recipe_section_pagination.py": (
        750,
        "REQ-CNST-010-E23: #4414 binds terminal completion receipts to the finalized page "
        "content digest inside the existing immutable page renderer so pagination and receipt "
        "identity cannot drift across separate serialization authorities. "
        "#4557 decomposes pagination into sibling modules (_recipe_section_planning, "
        "_recipe_section_rendering) with char-ceiling plumbing and dual-domain page fitting.",
    ),
    "tools_recipe.py": (
        750,
        "REQ-CNST-010-E25: #4557 decomposes get_recipe_section handler into "
        "tools_recipe.py (tool entry points) and tools/_recipe_section_handler.py "
        "(bounded-delivery pull handler and counter injection).",
    ),
    "tools_execution.py": (
        1800,
        "REQ-CNST-010-E8: execution tool handlers — run_cmd/run_python/run_skill are the "
        "three primary execution paths; fail-closed existence gate, empty-closure gate "
        "for fabricated skill name rejection, _check_backend_compat fail-closed gate "
        "with resolver-absent fallback via extract_skill_name, and fix-required hook "
        "dispatch gate add defense-in-depth checks; server-side recipe-read prohibition "
        "and write-target boundary guards add defense-in-depth gate checks; "
        "stale-path is_dir() guards on both init_session and replay-snapshot branches "
        "crash-close before executor when /dev/shm path has been reclaimed (+26 net lines); "
        "post-serialization validation gate at run_skill return site adds fail-closed "
        "ToolFailureEnvelope substitution for structurally degraded payloads (+13 net lines); "
        "R0 capability-driven routing: _skill_requires_claude computation, binary probe "
        "for fail-closed behavior, and composite log reason emission (+40 net lines); "
        "github_api_write capability: _aggregate_sandbox_overrides, _has_routing_capability, "
        "_get_routing_caps helpers extracted from run_skill handler body (+30 net lines); "
        "shape-aware _compute_write_prefixes (worktree cwd vs clone root) and "
        "WORKTREE_SKILLS dispatch preflight + _scope_covers_cwd helper (+35 net lines)"
        "; per-step explicit backend override resolution, binary probe gating, "
        "override source evidence, and structured logging (+49 net lines)"
        "; fail-closed error return for unregistered explicit backend override (+3 net lines)"
        "; closure-mode MCP tool parameters: closure_authority_path/hash/plan_paths/"
        "base_sha/diff_sha/target_sha threaded through run_skill handler for "
        "execution-layer gate (+30 net lines)"
        "; closure_report_root derivation after output_dir recipe auto-fill (+11 net lines)"
        "; kitchen-scoped _check_pipeline_deps fallback, _active_order_ids_for_kitchen "
        "multi-pipeline gating, _authority_blocks_dependency_check/"
        "_check_review_approach_plan_path gates, "
        "and ambiguous/empty step_name dependency-deny branches (+126 net lines)"
        "; server-authoritative step completion: _mark_step_complete_server_side helper "
        "called at the run_skill adjudication point, immutable explicit tracker target "
        "selection, and deny_envelope conversion of all pre-flight deny sites "
        "(#4293 pipeline tracker split-brain, +65 net lines)"
        "; attested recipe execution identity/template verification, structured skill-input "
        "binding, runtime-binding digest capture, and inventory-admission preflight keep all "
        "run_skill launch denial paths before command construction (+139 net lines)",
    ),
    "execution/backends/codex.py": (
        1300,
        "REQ-CNST-010-E9-narrowed: CodexBackend class alone is 1062 lines "
        "(cmd/cmd-spec grammar with build_skill_session_cmd/"
        "build_food_truck_cmd/build_interactive_cmd/"
        "validate_interactive_invocation/setup_session_dir), "
        "with the four cmd-builder methods tightly coupled to CodexBackend "
        "state. CodexBackend retains all five cmd-builder methods because each "
        "touches instance state (capabilities, env policy, flag vocabulary, "
        "session locator) and the cmd-spec grammar is the backend's authority "
        "boundary — splitting these would force a separate mutable state object "
        "and break the protocol. The remaining slimmed file is 1242 "
        "lines; cap lowered from 2500 to 1300 to acknowledge the architectural seam that "
        "the decomposition could not cross without breaking the backend "
        "dataclass invariant.",
    ),
    "execution/backends/claude.py": (
        1600,
        "REQ-CNST-010-E19: Claude backend protocol parity keeps managed native-shell "
        "decision/reference disposition beside executable launch-binding validation; "
        "both are shared builder-interface obligations even though Claude deliberately "
        "does not inject the Codex-only controls; REQ-SEM-ADAPT-001 semantic-plan "
        "adaptation remains on this registered backend so native child syntax and model "
        "alias resolution cannot drift into a second adapter registry; #4443 also threads "
        "parent sandbox authority through the shared no-op setup boundary and explorer "
        "dispatch rendering preserves the same backend-owned syntax authority; #4480 adds "
        "the plugin_dir launch-binding validation parameter for cross-backend signature "
        "parity; #4507 renders one named child per runtime topic (+6 net lines); "
        "#4233 keeps Claude task lifecycle normalization and immutable skill-session "
        "async hardening beside the backend parser and command builder that own them. "
        "#4557 adds Claude-only host-attestation env, version-derived annotation support, "
        "and frozen attestation env at all 4 launch sites; #4566 "
        "adds execution-role protocol parity while preserving Claude behavior (+3 net lines). "
        "Threads mcp_tool_timeout_sec through build_interactive_cmd, "
        "build_skill_session_cmd, build_food_truck_cmd, and build_resume_cmd to give Claude "
        "Code's client-side idle-abort timeout parity with the server-side anyio.fail_after "
        "ceiling (+2 net lines). REQ-017 (resolve-failures iteration 1) also adds an "
        "explicit mcp_tool_timeout_sec parameter to build_headless_cmd and injects the "
        "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT env var when given, plus hardens all four "
        "existing boundary checks with isinstance(mcp_tool_timeout_sec, (int, float)) "
        "so MagicMock-bearing test mocks no longer raise at the builder (+19 net lines).",
    ),
    "execution/headless/_headless_result.py": (
        900,
        "REQ-CNST-010-E25-narrowed: #4233 keeps the async-obligation success gate adjacent to "
        "the existing stale, idle, timeout, and content adjudication order it must preempt. "
        "After #4664 decomposition, adjudication helpers live in _headless_adjudication.py "
        "— including the #4641/#4644 _should_flag_cleanup_incomplete diagnostic shared by "
        "both SkillResult construction seams; _build_skill_result remains here as the "
        "headless orchestration authority. The 827-line residual is dominated by that "
        "single 741-line function, which owns the success-gate adjacency rule.",
    ),
    "workspace/skill_capabilities.py": (
        1120,
        "REQ-SEM-SCHEMA-001: versioned semantic declarations, closed-operation parsing, "
        "retired-key rejection, and precise per-skill diagnostics remain co-located at "
        "the sole skill-frontmatter validation boundary; #4507 parses runtime child "
        "cardinality at that same boundary and classifies its dedicated typed invalidity "
        "before the general semantic-plan failure path.",
    ),
    "workspace/skills.py": (
        1550,
        "REQ-SEM-SCHEMA-002: semantic-plan threading and invalid-override fallback remain "
        "inside the existing precedence resolver so a rejected project-local declaration "
        "cannot poison unrelated skills or bypass the valid bundled fallback; typed "
        "invalidity and exclusion records remain adjacent to the resolver transitions "
        "whose rejected candidates they describe; "
        "REQ-CNST-010-E20: exploration-vector frontmatter parsing, canonical marker "
        "binding, and exact migrated-body replacement stay beside the SKILL.md parser so "
        "discovery and projection share one fail-closed content authority. Bumped to 1350 "
        "by the exploration-vector sidecar migration: exploration.yaml loading and the "
        "slim-schema sidecar parser stay beside the marker binder and frontmatter parser "
        "they feed so the sidecar digest, migrated/retained vector shapes, and marker "
        "contract remain one fail-closed parsing authority. Bumped to 1550 by typed "
        "skill-invalidity threading and the completed explorer sidecar migration.",
    ),
    "execution/backends/_codex_session_storage.py": (
        1500,
        "REQ-CNST-010-E13-narrowed: CodexSessionStore + CodexInteractiveSessionLease + "
        "_FileLease transaction-boundary core only; stateless FS primitives extracted to "
        "_codex_fs_atomic.py (RE: #4664). The transaction-boundary core remains one "
        "lock-coupled module — splitting _FileLease / CodexInteractiveSessionLease / "
        "CodexSessionStore across multiple files would duplicate the inode-preserving "
        "staging, process/thread/view leases, promotion, index publication, manifest "
        "validation, crash recovery, and explicit legacy-view reconciliation invariants. "
        "Cap lowered to 1500 lines to accommodate the core without the stateless helpers. "
        "#4678 rectify adds spawn-identity capture to _record_spawn and verify-before-mark "
        "identity checks to recover() — both belong to the same transaction boundary as "
        "the leases they gate, and fit under this cap post-extraction.",
    ),
    "workspace/session_skills.py": (
        1400,
        "REQ-CNST-010-E13/E14: ordering-sensitive session skill materialization owns "
        "provider discovery, override precedence, filtering, dependency activation, the "
        "generated-home lease and cleanup transaction, and backend-specific layout "
        "validation; keeping those operations together preserves both assembly ordering "
        "and the create/validate/yield/delete ownership proof",
    ),
    "rules_skill_content.py": (
        1200,
        "REQ-CNST-010-E11: SKILL.md content validation rules registry — accumulating "
        "semantic rules (undefined-bash-placeholder, hardcoded-origin-remote, "
        "blind-git-add, no-interpreter-mediated-writes, no-autoskillit-import, "
        "no-posix-char-class, no-grep-bre-alternation, output-section-no-markdown-directive, "
        "no-gh-issue-comment, transition-boundary-anti-confirmation, "
        "executable-field-content-validity, reviews-post-requires-input-flag, "
        "source-attribution-directive, graphql-query-requires-shell-invocation, "
        "inline-content-in-subagent-prompt) co-located to keep SKILL.md validation "
        "discovery a single import; splitting into sub-modules per rule would fragment "
        "the @semantic_rule registration surface and break the test filter cascade."
        "; inline-content-in-subagent-prompt rule (#4289 manifestation, #3636 architectural): "
        "extract_blockquote_sections + extract_blockquote_placeholders helpers co-located "
        "in _skill_placeholder_parser.py and re-used by both rules_skill_content.py "
        "and the tests/skills/ contract linters (+~60 net lines)",
    ),
    "core/types/_type_constants_registries.py": (
        1100,
        "REQ-CNST-010-E16: canonical immutable registries and their measured digests remain "
        "co-located so the #4411 execution-install-site binding cannot drift from the other "
        "tool and delivery registries that define its surfaces.",
    ),
    "core/context_admission.py": (
        3050,
        "REQ-CNST-010-E13: #4333 freezes one exhaustive protocol-v1 reducer and replay "
        "surface. Keeping all closed event transitions together makes atomic batch, "
        "idempotency, protected-pool, reconciliation, rollover, and declarative effect "
        "semantics, released-version dispatch, and configuration-aware coverage resolution "
        "reviewable as one state machine; splitting dispatch branches would fragment "
        "exhaustiveness.",
    ),
    "core/types/_type_context_admission.py": (
        2350,
        "REQ-CNST-010-E14: #4333 freezes the complete content-free protocol-v1 schema in "
        "one IL-0 shard. Co-locating identities, records, closed event/effect unions, "
        "states, canonical serialization, and the static coverage registry with its pinned "
        "configuration variants prevents downstream layers from defining incompatible "
        "wire contracts.",
    ),
    "pipeline/context_admission_ledger.py": (
        2300,
        "REQ-CNST-010-E15: #4334 keeps the crash-safe SQLite transaction boundary, "
        "journal replay verification, sticky health fencing, and exhaustive shadow "
        "projection in one IL-1 authority; consistent recovery snapshots and shared "
        "row/byte budgets remain beside replay validation so storage and reducer "
        "publication invariants cannot drift across independently mutable modules.",
    ),
    "pipeline/audit_admission_ledger.py": (
        2300,
        "REQ-CNST-010-E17: #4419 keeps installation fencing, reservation and attempt "
        "transitions, trusted head/preflight publication, disposition CAS, and recovery "
        "inside one crash-safe SQLite authority. Splitting the transactional state machine "
        "would let independently mutable storage paths drift from its atomic publication "
        "and fail-closed health invariants.",
    ),
    "server/tools/tools_execution.py": (
        2800,
        "REQ-CNST-010-E18: #4419 keeps the attested reservation, dispatch, exhaustive "
        "materialization outcome routing, and durable response finalization at the existing "
        "run_skill transaction boundary. Splitting that control flow would separate success "
        "bookkeeping from the ledger state it must atomically finalize. Managed native-shell "
        "lineage preparation remains at that same attested launch boundary so runtime "
        "binding and child construction cannot select different modes; specialized explorer "
        "projection and execution-identity persistence remain at the same admission boundary. "
        "#4457 keeps receipt drafting beside exhaustive run_skill terminal projection so "
        "every post-launch classification passes through one finalization path.",
    ),
    "hook_registry.py": (
        1200,
        "REQ-CNST-010-E21: hook_registry.py is a stdlib-only, package-root module imported "
        "directly by standalone hook subprocess scripts, so it deliberately stays a flat "
        "module rather than a sub-package (a package split would change how hook scripts "
        "resolve the import on the low-latency startup path). Relocatable hook commands "
        "(${CLAUDE_PLUGIN_ROOT} token generation in _build_hook_command, "
        "relocatable command rendering, and token-aware find_broken_hook_scripts/"
        "validate_plugin_cache_hooks) add 114 net lines to the existing registry+drift-"
        "detection surface. #4512 adds the exact exploration request-identity hook and "
        "its lifecycle resource contract to the same canonical registry.",
    ),
    "pipeline/exploration_context.py": (
        1100,
        "REQ-CNST-010-E22: explorer context store owns the process-local capability "
        "lifecycle for brokered exploration; #4488/#4489/#4492 add shared eligibility "
        "predicate is_explorer_binding_eligible, session-scoped Claude-native "
        "bind_session_scoped/session_scoped_capability authority mode, and the "
        "supporting lease management (+89 net lines)",
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
        rel = str(py_file.relative_to(SRC_ROOT))
        limit, _ = _LINE_LIMIT_EXEMPTIONS.get(
            rel, _LINE_LIMIT_EXEMPTIONS.get(py_file.name, (1000, ""))
        )
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
    """REQ-CNST-004: core/ modules must not import from any autoskillit sub-package.

    TYPE_CHECKING-guarded imports are permitted — they are zero-runtime-cost annotations
    that do not create actual import dependencies (same exemption as test_layer_enforcement.py).
    """
    core_dir = SRC_ROOT / "core"
    assert core_dir.exists(), "core/ package must exist"
    violations: list[str] = []
    for py_file in core_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        tc_lines: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                for stmt in node.body:
                    for child in ast.walk(stmt):
                        if isinstance(child, ast.Import | ast.ImportFrom):
                            tc_lines.add(child.lineno)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.lineno in tc_lines:
                    continue
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    violations.append(f"core/{py_file.name}:{node.lineno}: imports {node.module}")
            elif isinstance(node, ast.Import):
                if node.lineno in tc_lines:
                    continue
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
    isolated_files = ["_llm_triage.py", "version.py"]
    isolated_packages = ["smoke_utils"]
    forbidden_prefixes = ("autoskillit.server", "autoskillit.cli")
    violations: list[str] = []

    def _check_file(py_file: Path, label: str) -> None:
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if any(mod == f or mod.startswith(f + ".") for f in forbidden_prefixes):
                    violations.append(f"{label}:{node.lineno}: imports {mod}")

    for filename in isolated_files:
        py_file = SRC_ROOT / filename
        if not py_file.exists():
            continue
        _check_file(py_file, filename)

    for pkg_name in isolated_packages:
        pkg_dir = SRC_ROOT / pkg_name
        if not pkg_dir.is_dir():
            continue
        for py_file in sorted(pkg_dir.glob("*.py")):
            label = f"{pkg_name}/{py_file.name}"
            _check_file(py_file, label)

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
    for py_file in sorted((server_dir / "tools").glob("tools_*.py")):
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
    - plugin_authority: PluginArtifactAuthority (lifetime-owning authority)
    - config: AutomationConfig dataclass (configuration container, not a service interface)
    - recipe_initialization_state: lifecycle value union, not a service interface
    - kitchen_open_state: immutable lifecycle value, protected by KitchenTransitionLock
    """
    AUTOSKILLIT_ROOT = SRC_ROOT

    # Collect Protocol class names from core/types.py and its sub-modules via AST.
    # After the types.py split, Protocol definitions live in the _type_protocols_*.py
    # shards and SubprocessRunner lives in _type_subprocess.py; types.py is a thin re-export hub.
    core_protocols: set[str] = set()
    for types_filename in (
        "core/types/__init__.py",
        "core/types/_type_audit_admission.py",
        "core/types/_type_audit_admission_ledger.py",
        "core/types/_type_audit_protocols.py",
        "core/types/_type_protocols_logging.py",
        "core/types/_type_protocols_execution.py",
        "core/types/_type_protocols_github.py",
        "core/types/_type_protocols_workspace.py",
        "core/types/_type_protocols_recipe.py",
        "core/types/_type_protocols_infra.py",
        "core/types/_type_protocols_backend.py",
        "core/types/_type_recipe_execution.py",
        "core/types/_type_subprocess.py",
        "core/types/_type_context_admission_persistence.py",
        "core/types/_type_native_shell_capture.py",
        "core/types/_type_exploration.py",
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

    EXEMPT = {
        "config",
        "active_recipe_packs",
        "active_recipe_features",
        "active_recipe_steps",
        "active_recipe_ingredients",
        "recipe_initialization_state",
        "recipe_terminal_response_cache",
        "kitchen_open_state",
        "kitchen_process_identity",
        "kitchen_tracker_key",
        "tracker_leases",
        "tracker_leases_lock",
        "temp_dir",
        "project_dir",
        "ephemeral_root",
        "_baseline_config",
        "_session_config_overrides",
    }
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

    assert _check_outdated_version.__module__ == "autoskillit.recipe.rules.rules_inputs"


def test_installed_version_in_core_types() -> None:
    """P3-F2: AUTOSKILLIT_INSTALLED_VERSION must be in autoskillit.core."""
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    assert isinstance(AUTOSKILLIT_INSTALLED_VERSION, str) and AUTOSKILLIT_INSTALLED_VERSION


def test_rule_submodules_no_autoskillit_init_import() -> None:
    """P3-F2: rules_*.py submodules must not import from autoskillit top-level __init__."""
    rule_files = sorted((SRC_ROOT / "recipe" / "rules").rglob("rules_*.py"))
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


def test_recipe_lister_callsites_use_protocol_typing() -> None:
    """REQ-ARCH-006: callsites in recipe/ that consume the skill listing
    must reference the SkillLister Protocol (parameter type), so the
    deferred DefaultSkillResolver() instantiation is a default-factory fallback
    rather than the only path.

    contracts.py uses .resolve() and therefore references SkillResolver,
    not SkillLister. That is checked separately below.
    """
    lister_targets = {
        "src/autoskillit/recipe/_skill_helpers.py",
        "src/autoskillit/recipe/_api.py",
    }
    src_root = Path(__file__).resolve().parents[2]
    missing: list[str] = []
    for relpath in lister_targets:
        text = (src_root / relpath).read_text()
        if "SkillLister" not in text:
            missing.append(relpath)
    assert not missing, (
        f"These files still consume SkillResolver without SkillLister Protocol typing: {missing}"
    )
    # contracts.py uses .resolve() — must reference SkillResolver, not SkillLister
    contracts_text = (src_root / "src/autoskillit/recipe/_contracts_staleness.py").read_text()
    assert "SkillResolver" in contracts_text, (
        "_contracts_staleness.py must reference SkillResolver for the resolver parameter"
    )


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
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "asyncio.create_task(" not in source  # REQ-SIG-001

    def test_no_asyncio_wait_call(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "asyncio.wait(" not in source  # REQ-SIG-001

    def test_no_asyncio_import_at_runtime(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "import asyncio" not in source  # REQ-SIG-001

    def test_anyio_create_task_group_present(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "anyio.create_task_group()" in source  # REQ-SIG-002

    def test_scan_done_signals_absent(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "def scan_done_signals(" not in source  # REQ-SIG-003

    def test_race_accumulator_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "class RaceAccumulator" in source  # REQ-SIG-003

    def test_cancel_scope_cancel_present(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "cancel_scope.cancel()" in source  # REQ-SIG-004

    def test_resolve_termination_preserved(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "def resolve_termination(" in source  # REQ-SIG-005

    def test_channel_b_drain_wait_uses_move_on_after(self):
        source = Path("src/autoskillit/execution/process/__init__.py").read_text()
        assert "anyio.move_on_after(" in source  # REQ-SIG-006

    def test_watch_process_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_process(" in source  # REQ-SIG-007

    def test_watch_heartbeat_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_heartbeat(" in source  # REQ-SIG-007

    def test_watch_session_log_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_session_log(" in source  # REQ-SIG-007

    def test_watch_child_activity_present(self):
        source = Path("src/autoskillit/execution/process/_process_race.py").read_text()
        assert "async def _watch_child_activity(" in source  # REQ-SIG-007

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
            "idle_stall",
            "process_exited_event",
            "channel_b_orphaned_tool_result",
            "exit_snapshot",
            "inspector_verdict",
            "lifecycle_observation_complete",
            "pending_task_ids",
            "terminal_task_ids",
            "schedule_wakeup_violation",
            "completion_ceiling_expired",
            "process_observation_snapshot",
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


def test_singleton_exemption_comment_matches_both_windows() -> None:
    """The _install_info exemption comment in SINGLETON_ALLOWED_MODULES must
    accurately reflect both the _STABLE_DISMISS_WINDOW and _DEV_DISMISS_WINDOW values."""

    from autoskillit.cli.install._install_info import _DEV_DISMISS_WINDOW, _STABLE_DISMISS_WINDOW

    this_file = Path(__file__)
    content = this_file.read_text(encoding="utf-8")

    def _fmt_td(td: object) -> str:
        import datetime

        if not isinstance(td, datetime.timedelta):
            return repr(td)
        total_seconds = td.total_seconds()
        if total_seconds % 86400 == 0:
            return f"timedelta(days={int(total_seconds // 86400)})"
        if total_seconds % 3600 == 0:
            return f"timedelta(hours={int(total_seconds // 3600)})"
        return repr(td)

    stable_fragment = _fmt_td(_STABLE_DISMISS_WINDOW)
    dev_fragment = _fmt_td(_DEV_DISMISS_WINDOW)

    assert stable_fragment in content, (
        f"Exemption comment in SINGLETON_ALLOWED_MODULES is stale. "
        f"Expected to find '{stable_fragment}' "
        f"(current _STABLE_DISMISS_WINDOW={_STABLE_DISMISS_WINDOW!r}). "
        "Update the comment on the '_install_info' entry."
    )
    assert dev_fragment in content, (
        f"Exemption comment in SINGLETON_ALLOWED_MODULES is stale. "
        f"Expected to find '{dev_fragment}' "
        f"(current _DEV_DISMISS_WINDOW={_DEV_DISMISS_WINDOW!r}). "
        "Update the comment on the '_install_info' entry."
    )


def test_update_checks_docstring_describes_both_windows() -> None:
    """The _update_checks module docstring and _is_dismissed docstring must
    mention both branch-aware window values."""
    import ast

    src_root = Path(__file__).parent.parent.parent / "src"
    module_path = src_root / "autoskillit" / "cli" / "update" / "_update_checks.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    module_doc = ast.get_docstring(tree) or ""
    assert "timedelta(days=7)" in module_doc or "days=7" in module_doc, (
        "_update_checks module docstring must mention the 7-day stable window"
    )
    assert "timedelta(hours=12)" in module_doc or "hours=12" in module_doc, (
        "_update_checks module docstring must mention the 12-hour dev window"
    )

    # Also verify _is_dismissed has a docstring mentioning both windows
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_is_dismissed":
            func_doc = ast.get_docstring(node) or ""
            assert "days=7" in func_doc or "7 days" in func_doc, (
                "_is_dismissed docstring must mention the 7-day window"
            )
            assert "hours=12" in func_doc or "12 hours" in func_doc, (
                "_is_dismissed docstring must mention the 12-hour window"
            )
            break


# ---------------------------------------------------------------------------
# Decomposition sibling-set guards for GitHub issue #4663
#
# These tests pin the exact set of submodules each decomposed package
# contains. Adding or removing a sibling submodule is a structural decision
# that must be reviewed (the registry tracer warns when test sites rebind
# attributes via these exact module paths).
# ---------------------------------------------------------------------------


def test_response_budget_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "_response_budget"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_primitives",
        "_projection",
        "_spill",
        "_enforce",
    }


def test_execution_helpers_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "_execution_helpers"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_skill_contract",
        "_dispatch_metadata",
        "_run_cmd_spill",
        "_run_python_coercion",
    }


def test_evidence_reader_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "_evidence_reader"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_authority",
        "_invocation",
        "_reader",
        "_startup",
    }


def test_tools_kitchen_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_kitchen"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_open_kitchen",
        "_open_kitchen_transition",
        "_open_kitchen_errors",
        "_close_kitchen",
        "_lock_ingredients",
        "_reload_session",
        "_disable_quota_guard",
        "_get_recipe",
        "_hook_config",
        "_tracker_authority",
        "_declare_join_batch",
    }


def test_tools_fleet_dispatch_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_provenance",
        "_campaign_state",
        "_handlers",
    }


def test_tools_pipeline_tracker_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_pipeline_tracker"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_authority",
        "_status",
        "_handlers",
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Tools-execution package decomposition is a multi-part plan; this PR adds "
        "the _RunSkillDispatchState dataclass (Step 1) but Steps 2-3 — the package "
        "__init__.py plus _run_cmd, _run_python, _run_skill_dispatch, _run_skill_finalize "
        "siblings — land in subsequent PRs. Tracking issue #4677."
    ),
)
def test_tools_execution_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "tools" / "tools_execution"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_run_cmd",
        "_run_python",
        "_run_skill_dispatch",
        "_run_skill_finalize",
        "_state",
    }


def test_lifespan_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "server" / "_lifespan"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_startup_checks",
        "_session_boots",
        "_lifespan",
    }


def test_prompts_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "prompts"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_prompts",
        "_prompts_campaign",
        "_prompts_kitchen",
        "_prompts_orchestrator",
    }


def test_install_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "install"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_install_contract",
        "_install_info",
        "_installed_plugins",
        "_marketplace",
        "_plugin_artifact",
    }


def test_session_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "session"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_session_backend",
        "_session_constants",
        "_session_cook",
        "_session_launch",
        "_session_onboarding",
        "_session_order",
        "_session_picker",
        "_session_process",
        "_session_reload",
        "_session_startup_trace",
    }


def test_update_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "update"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_obligation_repair",
        "_transaction",
        "_update",
        "_update_checks",
        "_update_checks_fetch",
        "_update_checks_source",
        "_restart",
    }


def test_ops_decomposition_has_expected_siblings() -> None:
    pkg = SRC_ROOT / "cli" / "ops"
    assert {p.name.removesuffix(".py") for p in pkg.glob("*.py")} == {
        "__init__",
        "_capture_store",
        "_codex_attempts",
        "_codex_orphans",
        "_daemon_orphans",
        "_process_orphans",
        "_sessions",
    }


@pytest.mark.parametrize(
    "facade_pkg",
    ["autoskillit.cli.prompts", "autoskillit.cli.ops", "autoskillit.cli.install"],
)
def test_cli_facade_all_resolves(facade_pkg: str) -> None:
    """Guard: facade ``__all__`` entries resolve and match submodule declarations.

    Forward direction (always covered): every name declared in the facade's
    ``__all__`` must resolve via ``hasattr`` — otherwise ``from autoskillit.cli.X
    import <name>`` raises ``ImportError``, the import form used by virtually
    every consumer.

    Reverse direction (covered for submodules that declare ``__all__``): when a
    submodule declares an ``__all__``, every entry must also appear in the
    facade's ``__all__`` and resolve to the same object. This catches drift
    where a builder is added to one layer (e.g. ``_prompts.py``) but not the
    other (e.g. ``prompts/__init__.py``), leaving the two lists silently out of
    sync. Submodules without ``__all__`` are not reverse-checked here — they
    rely on the forward-only ``hasattr`` check and on the existing
    ``TestPromptsReExporter`` guard for the inner-hub case.
    """
    import importlib

    facade = importlib.import_module(facade_pkg)
    declared = set(getattr(facade, "__all__", ()))
    assert declared, f"{facade_pkg}.__all__ is empty or missing"

    # Forward direction: every declared name must resolve.
    missing = sorted(name for name in declared if not hasattr(facade, name))
    assert not missing, f"{facade_pkg} __all__ lists names that do not resolve: {missing}"

    # Reverse direction: where declared, lazy-loaded entries must resolve to
    # the same object as the submodule attribute. ``_*.py`` glob also matches
    # ``__init__.py`` itself — skip the self-comparison.
    pkg_dir = SRC_ROOT / facade_pkg.replace("autoskillit.", "").replace(".", "/")
    for submodule_path in pkg_dir.glob("_*.py"):
        if submodule_path.name == "__init__.py":
            continue
        submodule_name = submodule_path.stem
        submodule = importlib.import_module(f"{facade_pkg}.{submodule_name}")
        for name in getattr(submodule, "__all__", ()):
            if name not in declared:
                assert False, (
                    f"{facade_pkg}.{submodule_name}.{name!r} is in submodule __all__ "
                    f"but missing from facade __all__"
                )
            facade_value = getattr(facade, name)
            submodule_value = getattr(submodule, name)
            assert facade_value is submodule_value, (
                f"{facade_pkg}.{name!r} resolves to a different object than "
                f"{submodule_name}.{name!r}"
            )
