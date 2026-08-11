"""C-I3: Architectural guard — hook-artifact durable writes are registered.

Scans the specific modules already known to own hook-artifact publication or
repair logic (hooks.json, ``~/.claude/settings.json`` hook entries,
``~/.codex/config.toml`` hook entries) for direct syntactic persistence call
sites, and asserts each is either registered in ``DURABLE_ARTIFACT_WRITERS``
or explicitly named in ``_NON_HOOK_ALLOWLIST`` as a non-hook write that
happens to live in the same module.

Completeness claim (stated honestly): this guard is deliberately scoped to
the modules already known to publish or repair hook artifacts — it is not a
codebase-wide sweep of every ``atomic_write``/``write_versioned_json`` call
site. A codebase-wide sweep was evaluated and rejected: it surfaces roughly
140 unrelated durable-write call sites (session logs, quota caches, planner
manifests, fleet state, migration bookkeeping, ...) that have nothing to do
with hook-artifact relocatability, which would turn this guard into a
maintenance tax on every unrelated persistence change in the repository.
Within its scope, the guard mechanically finds direct syntactic call sites —
``atomic_write``, ``write_versioned_json``, ``json.dump``, ``shutil.copy2``,
``shutil.copytree``, ``Path.write_text``, ``Path.write_bytes`` — skipping
destinations that are plainly temp/scratch-scoped. Indirect or dynamic
writes (reflection, ``exec``, subprocess-mediated writes) are outside its
proof surface. Extending ``_SCOPED_MODULES`` is the intended way to bring a
newly hook-artifact-owning module under this guard's enforcement.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants import DURABLE_ARTIFACT_WRITERS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

#: Modules known to own hook-artifact publication or repair logic — the only
#: files this guard scans. A module that starts writing a durable hook
#: artifact (hooks.json, settings.json hook entries, config.toml hook
#: entries) must be added here before its write call sites are enforced.
_SCOPED_MODULES: tuple[str, ...] = (
    "workspace/_projected_artifact/materialization.py",
    "workspace/_projected_artifact/_hook_repair.py",
    "cli/_hooks.py",
    "execution/backends/_codex_hooks.py",
    "execution/backends/_codex_config.py",
    "server/_lifespan.py",
)

#: Names of persistence functions/methods whose call sites count as a durable
#: write for this guard.
_PERSISTENCE_CALL_NAMES: frozenset[str] = frozenset(
    {
        "atomic_write",
        "write_versioned_json",
        "dump",
        "copy2",
        "copytree",
        "write_text",
        "write_bytes",
    }
)

#: Substrings in a destination expression's source text that mark it as
#: temp/scratch-scoped and therefore exempt from registration.
_TEMP_INDICATORS: tuple[str, ...] = (
    "tempfile",
    "tmp_path",
    "staging",
    ".autoskillit/temp",
    "TemporaryDirectory",
)

#: Write sites inside a scoped module that are demonstrably NOT hook-artifact
#: writers. Destination-text alone can't distinguish "SKILL.md" from
#: "hooks.json" within the same module, so these are named explicitly with a
#: one-line rationale rather than folded into DURABLE_ARTIFACT_WRITERS.
_NON_HOOK_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # Projected SKILL.md documents — not a hook artifact.
        ("workspace/_projected_artifact/materialization.py", "materialize_agent_skill_tree"),
        # Rewritten agent .md frontmatter (MCP tool prefixes) — not a hook artifact.
        ("workspace/_projected_artifact/materialization.py", "_render_agent_definitions"),
        # Projection manifest.json (digests only, no hook paths) — not a hook artifact.
        ("workspace/_projected_artifact/materialization.py", "materialize_sanitized_plugin_root"),
        # Verbatim byte-for-byte asset copy — no path is baked into the copy.
        ("workspace/_projected_artifact/materialization.py", "_copy_non_skill_plugin_assets"),
        # Bare TOML scalars (tool_output_token_limit, auto-compact limit) — unrelated to hooks.
        ("execution/backends/_codex_config.py", "_ensure_top_level_key"),
        ("execution/backends/_codex_config.py", "_upsert_top_level_key_exact"),
    }
)

_REGISTERED_WRITERS: frozenset[str] = frozenset(w.writer for w in DURABLE_ARTIFACT_WRITERS)


def _module_path(rel: str) -> str:
    return "autoskillit." + rel.removesuffix(".py").replace("/", ".")


def _find_enclosing_func(tree: ast.Module, target_lineno: int) -> str | None:
    """Return the innermost function/method whose body spans *target_lineno*."""
    best: str | None = None
    best_line = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= target_lineno:
                end = getattr(node, "end_lineno", node.lineno)
                if end >= target_lineno and node.lineno > best_line:
                    best, best_line = node.name, node.lineno
    return best


def _destination_node(node: ast.Call, call_name: str) -> ast.AST | None:
    """Return the AST node for the write destination, keyed by call shape."""
    if call_name in ("copy2", "copytree"):
        return node.args[1] if len(node.args) > 1 else None
    if call_name in ("write_text", "write_bytes"):
        return node.func.value if isinstance(node.func, ast.Attribute) else None
    if call_name == "dump":
        # json.dump(obj, fp) -- destination is the file-object arg, not the payload.
        return node.args[1] if len(node.args) > 1 else None
    return node.args[0] if node.args else None


def _is_temp_scoped(node: ast.AST | None, source: str) -> bool:
    if node is None:
        return False
    try:
        text = ast.get_source_segment(source, node) or ""
    except Exception:
        return False
    return any(indicator in text for indicator in _TEMP_INDICATORS)


def _scan_module(rel: str) -> list[tuple[int, str, str]]:
    """Return (lineno, call_name, enclosing_function) for each write call site."""
    path = SRC_ROOT / rel
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name: str | None = None
        if isinstance(node.func, ast.Name) and node.func.id in _PERSISTENCE_CALL_NAMES:
            call_name = node.func.id
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _PERSISTENCE_CALL_NAMES:
            call_name = node.func.attr
        if call_name is None:
            continue
        if call_name == "dump" and not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
        ):
            continue  # only json.dump(obj, fp) counts; other .dump() methods aren't file writes
        dest = _destination_node(node, call_name)
        if _is_temp_scoped(dest, source):
            continue
        func_name = _find_enclosing_func(tree, node.lineno)
        if func_name is None:
            continue  # module-level write call -- none exist in scoped modules today
        findings.append((node.lineno, call_name, func_name))
    return findings


def test_no_unregistered_hook_artifact_write_sites() -> None:
    """Every durable write call site in a hook-artifact-owning module is accounted for."""
    unregistered: list[str] = []
    for rel in _SCOPED_MODULES:
        module_path = _module_path(rel)
        for lineno, call_name, func_name in _scan_module(rel):
            writer_key = f"{module_path}:{func_name}"
            if writer_key in _REGISTERED_WRITERS:
                continue
            if (rel, func_name) in _NON_HOOK_ALLOWLIST:
                continue
            unregistered.append(
                f"  {rel}:{lineno} in {func_name}() calls {call_name}() -- "
                f"add {writer_key!r} to DURABLE_ARTIFACT_WRITERS or "
                f'("{rel}", "{func_name}") to _NON_HOOK_ALLOWLIST'
            )
    assert not unregistered, "Unregistered hook-artifact write sites found:\n" + "\n".join(
        unregistered
    )


def test_scoped_modules_exist() -> None:
    """A stale or typo'd path would otherwise silently scan zero files."""
    missing = [rel for rel in _SCOPED_MODULES if not (SRC_ROOT / rel).is_file()]
    assert not missing, f"_SCOPED_MODULES references missing files: {missing}"


def test_registered_writers_have_a_matching_call_site() -> None:
    """Registry-drift guard for the inverse direction.

    Every DURABLE_ARTIFACT_WRITERS entry whose module falls inside
    _SCOPED_MODULES must correspond to an actual write call site found by the
    scan -- catches a renamed or removed function whose registry entry
    silently went stale.
    """
    found: set[str] = set()
    for rel in _SCOPED_MODULES:
        module_path = _module_path(rel)
        for _lineno, _call_name, func_name in _scan_module(rel):
            found.add(f"{module_path}:{func_name}")
    scoped_module_paths = {_module_path(rel) for rel in _SCOPED_MODULES}
    stale = [
        w.writer
        for w in DURABLE_ARTIFACT_WRITERS
        if w.writer.rsplit(":", 1)[0] in scoped_module_paths and w.writer not in found
    ]
    assert not stale, (
        "DURABLE_ARTIFACT_WRITERS entries with no matching write call site in their "
        f"scoped module (stale registration?): {stale}"
    )


def test_codex_reconciliation_audit_no_clobber_writer_is_registered() -> None:
    """The immutable audit hard-link publisher remains a registered durable writer."""
    rel = "execution/backends/_codex_session_storage.py"
    path = SRC_ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    writer = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_write_reconciliation_audit"
    )
    calls = {
        node.func.attr
        for node in ast.walk(writer)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {"link", "fsync", "unlink"} <= calls
    assert (
        "autoskillit.execution.backends._codex_session_storage:_write_reconciliation_audit"
    ) in _REGISTERED_WRITERS
