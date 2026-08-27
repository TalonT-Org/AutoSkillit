from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import _SOURCE_FILES, _rel

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _get_call_func_name(node: ast.Call) -> str | None:
    """Return the function name for simple calls, or None for complex expressions."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


SINGLETON_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "__init__",  # server/__init__.py: mcp = FastMCP(...)
        "_fleet",  # cli/_fleet.py: fleet_app = App(...)
        "app",  # cli/app.py: app = App(...), config_app = App(...), etc.
        "store",  # migration/store.py: defensive exemption for future module-level construction
        "validator",  # recipe/validator.py: defensive exemption for decorator-based rule registry
        "settings",  # config/settings.py: _CONFIG_SCHEMA = _build_config_schema()
        "_validation",  # config/_validation.py: _CONFIG_SCHEMA = _build_config_schema() (#4859)
        "_headless_path_tokens",  # execution/_headless_path_tokens.py: _OUTPUT_PATH_TOKENS
        "_probe_cache",  # execution/backends/_probe_cache.py: PROBE_CACHE_TTL = timedelta(...)
        # Typed cancellation state is required for the authenticated broker boundary (#4585).
        "tools_evidence_reader",
        # _STAGING_ORPHAN_GRACE = timedelta(hours=1)
        "_generation_publication",
        # Legacy installed-plugin retirement uses a six-hour enqueue deadline.
        "_install_state",
        # _STABLE_DISMISS_WINDOW = timedelta(days=7), _DEV_DISMISS_WINDOW = timedelta(hours=12)
        "_install_info",  # cli/install/_install_info.py: window constants (see comment above)
        # KITCHEN_GUARDED_COMMANDS: frozenset[str]
        "_update_checks",  # cli/_update_checks.py: module-level frozenset (see comment above)
        # _HTTP_TIMEOUT = httpx.Timeout(...) — module-level httpx client timeout config
        "_update_checks_fetch",  # cli/_update_checks_fetch.py: _HTTP_TIMEOUT constant
        "_terminal",  # cli/_terminal.py: _BASE_RESET = "".join(...) derived from _RESET_SPEC
        "_reconcile",  # hooks/_capture/_reconcile.py: immutable owner budget contracts
        "skill_capability_cache",  # bounded weighted-LRU cache singleton bound once at import time
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
        "_type_recipe_sections",  # recipe registry and pagination-policy construction
        # Issue #4735 — Wavefront 1 decomposition. The retirement shard builds
        # _ABSOLUTE_ARTIFACT_KEYS = sorted(...) at import time so the absolute-path
        # guard can run at module load.
        "_type_constants_retirements",
        # Issue #4735 — Wavefront 1 decomposition. The skill-contract shard runs
        # _UNREGISTERED_INVALIDITY_KINDS = sorted(set(SkillInvalidityKind) - set(...))
        # at import time as the completeness-vs-enum assertion.
        "_type_constants_skill_contract",
        "_type_dimensions",  # named conversion policies (BytesToTokensPolicy instances)
        "tool_registry",  # immutable canonical MCP tool definition registry
        "_tool_registry_builders",  # immutable tool-role and definition construction
        # Frozen static ownership and identity-profile definitions are derived once.
        "_type_audit_admission_artifact_ownership",
        "_type_audit_admission_reference_identity",
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
        # _UNCLASSIFIED_EVIDENCE_SOURCES = sorted(...) at import time -- the completeness-
        # vs-enum self-check that every EvidenceSource has a _REVOCABILITY entry (S1-1).
        "_reclamation",  # core/runtime/_reclamation.py
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
        ("src/autoskillit/hooks/_capture/_types.py", "DEBT_ASSIST_BUDGET"),
        ("src/autoskillit/hooks/_capture/_types.py", "HOT_PATH_LOCK_WAIT"),
        ("src/autoskillit/hooks/_capture/_types.py", "REQUIRED_RETENTION_BYTES"),
        ("src/autoskillit/hooks/_capture/_types.py", "TRANSITION_RESCUE_BUDGET"),
        ("src/autoskillit/pipeline/_context_admission_ledger/_codec.py", "_EVENT_TYPES"),
        ("src/autoskillit/pipeline/_context_admission_ledger/_codec.py", "_EFFECT_TYPES"),
        ("src/autoskillit/pipeline/_context_admission_ledger/_codec.py", "_STATE_TYPES"),
        (
            "src/autoskillit/server/tools/tools_kitchen/_open_kitchen_transition.py",
            "_OPEN_KITCHEN_REQUEST_CTX",
        ),
        # A bare Path("/proc") constant, no I/O -- the default proc_root every
        # function below defaults its keyword-only proc_root parameter to (S1-1).
        ("src/autoskillit/core/runtime/_linux_proc.py", "_DEFAULT_PROC"),
    }
)


_MODULE_LEVEL_IO_FUNC_NAMES: frozenset[str] = frozenset({"load_config", "open"})


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
    tree = ast.parse(source, filename=str(path))

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
        if path == "src/autoskillit/pipeline/_context_admission_ledger/_codec.py"
    } == {"_EVENT_TYPES", "_EFFECT_TYPES", "_STATE_TYPES"}


def test_capture_types_singleton_is_path_and_assignment_scoped(tmp_path: Path) -> None:
    assert "_types" not in SINGLETON_ALLOWED_MODULES
    assert {
        target
        for path, target in _SINGLETON_SAFE_ASSIGNMENTS
        if path == "src/autoskillit/hooks/_capture/_types.py"
    } == {
        "DEBT_ASSIST_BUDGET",
        "HOT_PATH_LOCK_WAIT",
        "REQUIRED_RETENTION_BYTES",
        "TRANSITION_RESCUE_BUDGET",
    }
    unrelated = tmp_path / "_types.py"
    unrelated.write_text("TRANSITION_RESCUE_BUDGET = SweepBudgetSpec()\n")

    with pytest.raises(AssertionError, match="Singleton locality violations"):
        test_singleton_definition_locality(unrelated)


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


def test_no_module_level_io_rejects_unparseable_source(tmp_path: Path) -> None:
    f = tmp_path / "invalid.py"
    f.write_text("if True\n")

    with pytest.raises(SyntaxError):
        _scan_module_level_io(f)
