"""Generated hook session-scope authority tests."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import autoskillit.hooks  # noqa: F401  (populates the lazy hook registry)
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HOOKS_DIR,
    HookDef,
    hook_applies_to_backend,
    render_hook_scope_table,
)
from autoskillit.hooks._runtime import _hook_scope_table, _session_scope_authority
from autoskillit.workspace._projected_artifact._publication import write_generated_hooks_json
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def _registry_scopes() -> dict[str, str]:
    return {
        script: hook_def.session_scope for hook_def in HOOK_REGISTRY for script in hook_def.scripts
    }


def _run_copied_guard(
    tmp_path: Path,
    script: str,
    *,
    table_content: str | None,
) -> subprocess.CompletedProcess[str]:
    copied_hooks = tmp_path / "hooks"
    shutil.copytree(HOOKS_DIR, copied_hooks)
    table_path = copied_hooks / "_runtime" / "_hook_scope_table.py"
    if table_content is None:
        table_path.unlink()
    else:
        table_path.write_text(table_content, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-B", str(copied_hooks / script)],
        input=json.dumps({"tool_name": "AskUserQuestion", "tool_input": {}}),
        capture_output=True,
        text=True,
        env=production_interpreter_env(),
        timeout=10,
    )


def test_committed_scope_table_matches_registry_renderer() -> None:
    assert _hook_scope_table.HOOK_SCOPE_BY_SCRIPT == _registry_scopes()
    assert (HOOKS_DIR / "_runtime" / "_hook_scope_table.py").read_text(
        encoding="utf-8"
    ) == render_hook_scope_table()


@pytest.mark.parametrize("table_content", [None, "HOOK_SCOPE_BY_SCRIPT = {}\n"])
@pytest.mark.parametrize(
    "script",
    ["guards/ask_user_question_guard.py", "guards/mcp_health_advisor.py"],
)
def test_missing_scope_table_fails_closed(
    tmp_path: Path,
    script: str,
    table_content: str | None,
) -> None:
    result = _run_copied_guard(tmp_path, script, table_content=table_content)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    # Denial reason uses render_provenance_prefix + PolicyEvent; the
    # canonical reason_code surfaces as ``code=scope_authority_unavailable``.
    assert "code=scope_authority_unavailable" in reason


@pytest.mark.parametrize("scope", ["any", "headless_only", "interactive_only"])
@pytest.mark.parametrize("headless", [False, True])
def test_runtime_scope_mapping_matches_registry(
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    headless: bool,
) -> None:
    script = "guards/test_scope_authority.py"
    monkeypatch.setattr(_hook_scope_table, "HOOK_SCOPE_BY_SCRIPT", {script: scope})
    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    hook_def = HookDef(matcher="Bash", session_scope=scope)  # type: ignore[arg-type]
    assert _session_scope_authority.enforce_script_session_scope(
        script
    ) is hook_applies_to_backend(
        hook_def,
        backend="claude_code",
        session_scope="headless" if headless else "interactive",
    )


def test_hook_def_rejects_unknown_session_scope() -> None:
    with pytest.raises(ValueError, match="HookDef.session_scope"):
        HookDef(matcher="Bash", session_scope="unrecognized")  # type: ignore[arg-type]


def test_projection_publication_writes_generated_scope_table(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    (plugin_root / "hooks").mkdir(parents=True)

    write_generated_hooks_json(plugin_root)

    assert (plugin_root / "hooks" / "_runtime" / "_hook_scope_table.py").read_text(
        encoding="utf-8"
    ) == render_hook_scope_table()


_SESSION_CLASS_KEYS = frozenset({"AUTOSKILLIT_HEADLESS", "AUTOSKILLIT_SESSION_TYPE"})
_NON_SCOPE_DYNAMIC_KEYS = frozenset(
    {
        "NATIVE_SHELL_CAPTURE_MODE_ENV_VAR",
        "MANAGED_LAUNCH_ID_ENV_VAR",
        "MANAGED_ATTEMPT_ID_ENV_VAR",
        "MANAGED_LINEAGE_DIGEST_ENV_VAR",
        "MANAGED_LINEAGE_REF_ENV_VAR",
        "DISPATCH_ID_ENV_VAR",
    }
)
_EXPECTED_SCOPE_READS = {
    ("_runtime/_hook_settings.py", "AUTOSKILLIT_HEADLESS"),
    ("_runtime/_hook_settings.py", "AUTOSKILLIT_SESSION_TYPE"),
}


def _env_read_argument(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        owner = node.func.value
        if isinstance(owner, ast.Name) and owner.id == "os" and node.func.attr == "getenv":
            return node.args[0]
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
            and node.func.attr in {"get", "pop"}
        ):
            return node.args[0]
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
        ):
            return node.slice
    return None


def _scope_read_sites(source: str, filename: str) -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        argument = _env_read_argument(node)
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            if argument.value in _SESSION_CLASS_KEYS:
                sites.add((filename, argument.value))
        elif isinstance(argument, ast.Name) and argument.id in _NON_SCOPE_DYNAMIC_KEYS:
            continue
        elif argument is not None:
            sites.add((filename, "<unresolved>"))
    return sites


def test_no_guard_reads_session_class_env_directly() -> None:
    violations = {
        site
        for path in HOOKS_DIR.rglob("*.py")
        if path != HOOKS_DIR / "_runtime" / "_hook_settings.py"
        for site in _scope_read_sites(
            path.read_text(encoding="utf-8"), str(path.relative_to(HOOKS_DIR))
        )
    }
    assert not violations, f"session-class reads must use _hook_settings: {sorted(violations)}"


# Names of the deleted session-class wrappers — issue #5121 collapsed the
# (headless, tier) tuple reads into the canonical hook_session_shape() accessor,
# so any re-introduction of these names in a guard's import surface is a
# regression of the unified-API invariant.
_DELETED_SESSION_CLASS_WRAPPERS: frozenset[str] = frozenset(
    {"is_headless_session", "get_session_type"}
)


def _direct_assignment_names(node: ast.Assign | ast.AnnAssign) -> list[ast.Name]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target for target in targets if isinstance(target, ast.Name)]


def _imports_deleted_wrapper(node: ast.ImportFrom) -> bool:
    return any(
        alias.name == "*" or alias.name in _DELETED_SESSION_CLASS_WRAPPERS for alias in node.names
    )


def _wrapper_introduction_sites(source: str, filename: str) -> set[tuple[str, int]]:
    """Return ``(filename, lineno)`` for every deleted session-class wrapper re-introduction.

    Catches two bypass vectors that re-introduce the canonical surface
    through a different code path:

    1. ``from X import get_session_type`` (T2 ImportFrom vector) — also
       catches the rebind ``from X import get_session_type as g`` form
       (alias.asname is the bound name; the rebind case is checked via
       alias.name, which is always the source identifier).
    2. ``is_headless_session = ...`` / ``get_session_type = ...`` (T2
       PARTIAL Assign vector) — a top-level assignment that re-binds the
       deleted name to any expression. Without this scan, a future
       refactor could ``from _hook_settings import hook_session_shape as
       get_session_type`` and silently re-introduce the wrapper surface
       as a guard-local alias — defeating the unified-API invariant.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return set()
    sites: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Check imported source names (including wildcard imports), not aliases.
            if _imports_deleted_wrapper(node):
                sites.add((filename, node.lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if any(
                target.id in _DELETED_SESSION_CLASS_WRAPPERS
                for target in _direct_assignment_names(node)
            ):
                sites.add((filename, node.lineno))
    return sites


def test_no_guard_imports_deleted_session_class_wrappers() -> None:
    """No guard imports or re-binds the deleted is_headless_session / get_session_type wrappers.

    Issue #5121: the wrappers were deleted; their only successor is
    hook_session_shape(), which is the canonical atomic accessor. The scan
    blocks both the ImportFrom vector (T2) AND the Assign / AnnAssign
    rebind vector (T2 PARTIAL).
    """
    violations = {
        site
        for path in HOOKS_DIR.rglob("*.py")
        for site in _wrapper_introduction_sites(
            path.read_text(encoding="utf-8"), str(path.relative_to(HOOKS_DIR))
        )
    }
    assert not violations, (
        f"deleted session-class wrappers must not be imported or rebound: {sorted(violations)}. "
        "Use hook_session_shape() instead."
    )


# Runtime modules that historically defined the session-class wrappers
# (issue #5121 / T1). After the refactor, neither module should re-introduce
# top-level `def is_headless_session` / `def get_session_type` — the canonical
# accessor hook_session_shape() is the single source.
_RUNTIME_MODULE_PATHS = (
    HOOKS_DIR / "_runtime" / "_hook_settings.py",
    HOOKS_DIR / "_runtime" / "_session_scope_authority.py",
)


@pytest.mark.parametrize("module_path", _RUNTIME_MODULE_PATHS)
def test_no_wrapper_definitions_in_runtime_module(module_path: Path) -> None:
    """T1 — runtime modules must not define the deleted wrappers at module scope.

    Walks the module's top-level ``ast.FunctionDef`` nodes (not the full AST
    body, which would match nested functions defined for testing) and asserts
    none of them carries a deleted wrapper name. Catches any future
    re-introduction of the wrappers in the runtime layer.
    """
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))
    top_level_funcs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    for wrapper_name in _DELETED_SESSION_CLASS_WRAPPERS:
        assert wrapper_name not in top_level_funcs, (
            f"{module_path.relative_to(HOOKS_DIR)} defines a top-level "
            f"`def {wrapper_name}` — issue #5121 removed the wrapper; the "
            "canonical session-class accessor is hook_session_shape()."
        )


def test_session_class_env_read_inventory_is_complete() -> None:
    helper = HOOKS_DIR / "_runtime" / "_hook_settings.py"
    actual = {
        site
        for site in _scope_read_sites(
            helper.read_text(encoding="utf-8"), "_runtime/_hook_settings.py"
        )
        if site[1] != "<unresolved>"
    }
    assert actual == _EXPECTED_SCOPE_READS


def test_scope_read_sites_marks_dynamic_env_key_as_unresolved() -> None:
    assert _scope_read_sites("os.environ.get(dynamic_key)", "guards/canary.py") == {
        ("guards/canary.py", "<unresolved>")
    }


def test_adr_scope_claims_match_registry() -> None:
    docs_root = Path(__file__).resolve().parents[2] / "docs" / "decisions"
    claim_owners = {
        "0001-prohibit-background-subagent-execution.md": "guards/background_exec_guard.py",
        "0006-output-containment.md": "capture_lifecycle_hook.py",
    }
    claims = {
        path.name: match.group(1)
        for path in docs_root.glob("*.md")
        for match in re.finditer(
            r'session_scope="(any|headless_only|interactive_only)"',
            path.read_text(encoding="utf-8"),
        )
    }
    assert set(claims) == set(claim_owners)
    scopes = {script: hook.session_scope for hook in HOOK_REGISTRY for script in hook.scripts}
    assert all(claims[doc] == scopes[script] for doc, script in claim_owners.items())


def test_cross_layer_session_scope_values_match() -> None:
    """The IL-0 inline constant mirrors the canonical hook-runtime constant (T17).

    The IL-0 layer cannot import from autoskillit.hooks._runtime (hard constraint
    in core/types/AGENTS.md:7). The two layers must agree on the value set;
    a future addition (e.g. a fourth scope) requires updating both sites
    AND this test must be updated to reflect the new value. AST-based scan
    avoids the false-positive surface of substring matching (comments,
    docstrings, unrelated Literal usages).
    """
    from autoskillit.hooks._runtime._session_scope_authority import SESSION_SCOPE_VALUES

    core_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "autoskillit"
        / "core"
        / "types"
        / "_type_session_shape.py"
    )
    core_tree = ast.parse(core_path.read_text(encoding="utf-8"), filename=str(core_path))

    expected_tokens = frozenset({"any", "headless_only", "interactive_only"})
    found_tokens: set[str] = set()
    for node in ast.walk(core_tree):
        if not isinstance(node, ast.Set):
            continue
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                if elt.value in expected_tokens:
                    found_tokens.add(elt.value)
    assert found_tokens == expected_tokens, (
        f"IL-0 layer is missing session-scope values "
        f"{sorted(expected_tokens - found_tokens)} — the inline literal "
        "must mirror SESSION_SCOPE_VALUES (T17)."
    )
    assert SESSION_SCOPE_VALUES == expected_tokens


def test_enforce_script_session_scope_uses_hook_session_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enforce_script_session_scope() consults hook_session_shape() (T6 — headless branch).

    Setting AUTOSKILLIT_HEADLESS drives the canonical accessor through the
    env-var path; the result follows the scope:headless_only policy. The
    matching non-headless branch is exercised by
    ``test_enforce_script_session_scope_denies_non_headless_shape`` to
    keep each test free of inline delenv calls (the central scrub fixture
    already clears AUTOSKILLIT_HEADLESS at test start).
    """
    from autoskillit.hooks._runtime import _session_scope_authority

    monkeypatch.setattr(
        "autoskillit.hooks._runtime._hook_scope_table.HOOK_SCOPE_BY_SCRIPT",
        {"guards/fleet_dispatch_guard.py": "headless_only"},
    )

    # Headless shape — scope "headless_only" admits.
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    assert (
        _session_scope_authority.enforce_script_session_scope("guards/fleet_dispatch_guard.py")
        is True
    )


def test_enforce_script_session_scope_denies_non_headless_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enforce_script_session_scope() denies on the non-headless branch (T6 — deny branch)."""
    from autoskillit.hooks._runtime import _session_scope_authority

    monkeypatch.setattr(
        "autoskillit.hooks._runtime._hook_scope_table.HOOK_SCOPE_BY_SCRIPT",
        {"guards/fleet_dispatch_guard.py": "headless_only"},
    )
    # AUTOSKILLIT_HEADLESS is already cleared by the central scrub fixture.
    assert (
        _session_scope_authority.enforce_script_session_scope("guards/fleet_dispatch_guard.py")
        is False
    )


def test_admit_hook_session_scope_uses_session_scope_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patching the source of SESSION_SCOPE_VALUES changes admit_hook_session_scope (T5).

    Module-reference pattern in _hook_settings.py means admit_hook_session_scope
    does a fresh attribute lookup on _session_scope_authority at every call, so
    monkeypatching the source module's attribute (rather than a copy captured at
    import time) takes effect immediately.

    The lookup uses the bare-name 'import _session_scope_authority as _ssa' (the
    bare-name is resolved against hooks/_runtime/ on sys.path, matching the
    subprocess hook bootstrap). We register the canonical-dotted-name module
    under the bare name in sys.modules so the test's setattr takes effect on
    the same object that admit_hook_session_scope's lookup resolves to. The
    monkeypatch.setitem teardown restores the prior sys.modules entry so the
    mutation does not leak to later tests in the same xdist worker.
    """
    import autoskillit.hooks._runtime._session_scope_authority as canonical_mod

    # Force the bare-name entry in sys.modules to point at the canonical module
    # object. Earlier tests in the suite may have already populated
    # sys.modules['_session_scope_authority'] with a SEPARATE module object
    # loaded from the same source file; setdefault would be a no-op in that
    # case and the monkeypatch would land on the wrong object. The function
    # 'admit_hook_session_scope' consults the bare-name entry, so we must
    # ensure both keys resolve to the same module. monkeypatch.setitem
    # restores the prior value (or deletes the key) at test teardown.
    monkeypatch.setitem(sys.modules, "_session_scope_authority", canonical_mod)

    from autoskillit.hooks._runtime import _hook_settings

    monkeypatch.setattr(canonical_mod, "SESSION_SCOPE_VALUES", frozenset({"only_one_value"}))
    # With the canonical constant narrowed to a single value, the original
    # 'any' scope is no longer in the admitted set and admit raises ValueError.
    with pytest.raises(ValueError, match="Unknown hook session scope"):
        _hook_settings.admit_hook_session_scope("any", frozenset(), (True, "skill"))
    # The narrowed value itself admits the shape.
    assert (
        _hook_settings.admit_hook_session_scope("only_one_value", frozenset(), (True, "skill"))
        is True
    )


# Caller files that were migrated to hook_session_shape() in Step 4b. Each
# must import cleanly with the canonical accessor — this is the runtime
# behavioral gate that pairs with the static AST scan (T2 above).
_MIGRATED_CALLER_SCRIPTS = (
    "lint_after_edit_hook.py",
    "session_start_hook.py",
    "guards/write_guard.py",
    "guards/skill_load_guard.py",
    "guards/background_exec_guard.py",
    "guards/open_kitchen_guard.py",
    "guards/pr_create_guard.py",
    "guards/skill_orchestration_guard.py",
    "guards/fabricated_completion_guard.py",
)


def test_runtime_import_smoke_for_all_caller_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every migrated caller module imports cleanly (T15).

    sys.path entries are scoped via monkeypatch.syspath_prepend so the bare-name
    module lookups in the imported scripts do not leak across xdist workers —
    each worker restores sys.path to its prior state on teardown.
    """
    import importlib.util

    runtime_dir = HOOKS_DIR / "_runtime"
    if str(runtime_dir) not in sys.path:
        monkeypatch.syspath_prepend(str(runtime_dir))
    if str(HOOKS_DIR) not in sys.path:
        monkeypatch.syspath_prepend(str(HOOKS_DIR))

    failures: list[tuple[str, str]] = []
    for script_rel in _MIGRATED_CALLER_SCRIPTS:
        script_path = HOOKS_DIR / script_rel
        spec = importlib.util.spec_from_file_location(
            f"_migrated_{script_rel.replace('/', '_').replace('.py', '')}",
            script_path,
        )
        if spec is None or spec.loader is None:
            failures.append((script_rel, "spec_from_file_location returned None"))
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — import smoke test
            failures.append((script_rel, repr(exc)))
    assert not failures, f"migrated caller modules failed to import: {failures}"
