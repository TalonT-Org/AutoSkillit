"""AST ratchets: the two habits that produced this bug class stay unmergeable.

Symptom-scoped fixes have been tried in this seam repeatedly and have not held —
#1065 called itself "the 7th investigation in the hook/plugin lifecycle area",
#1786 named its own bug class as "the 3rd instance of dangling-absolute-path-
after-relocation" and then shipped a protocol scoped only to hook *script* paths,
never to the artifact roots those paths live under. The pattern is a reactive
repair with nothing structural requiring it to be applied again.

So these are ratchets, not reviews. Each has an allowlist that demands a written
rationale and a meta-test proving it actually fails on an injected violation —
modelled on `tests/infra/test_schema_read_convention.py`.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import pytest

pytestmark = [pytest.mark.medium]

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

#: Exact modules permitted to read `installed_plugins.json`. Every reader treats
#: registry data as evidence only; no execution path may derive a plugin source
#: or managed root from it.
REGISTRY_READ_ALLOWLIST: dict[str, str] = {
    "core/_plugin_ids.py": (
        "Defines registered_install_paths(), the stdlib evidence parser. Its output "
        "cannot authorize a source, launch path, or managed artifact root."
    ),
    "workspace/_install_state.py": (
        "Builds the current diagnostic obligation and compares exact retirement "
        "records with live registration evidence; trusted inputs derive every root."
    ),
    "workspace/_installed_artifact.py": (
        "The shared verifier rereads registry paths under the exact artifact lease only "
        "as obligation evidence; trusted home/plugin/version inputs derive its root."
    ),
}

#: Modules permitted to call `.resolve()` on a *write destination* before a
#: containment comparison. Only the shared primitive.
DESTINATION_RESOLVE_ALLOWLIST: dict[str, str] = {
    "core/paths.py": "Defines destination_location(), the shared primitive itself.",
}

PLUGIN_MUTATION_ALLOWLIST: dict[tuple[str, str, str], tuple[int, str]] = {
    ("server/tools/tools_kitchen.py", "_close_kitchen_handler", "hook_cfg_path.unlink"): (
        1,
        "Closing the kitchen removes the project-owned generated hook configuration; "
        "the next kitchen activation regenerates it.",
    ),
    ("server/tools/tools_kitchen.py", "_close_kitchen_handler", "overlay_path.unlink"): (
        1,
        "Closing the kitchen removes the project-owned session overlay while preserving "
        "its durable synchronization sidecar.",
    ),
    (
        "server/tools/tools_kitchen.py",
        "_close_kitchen_handler",
        "review_gate_path.unlink",
    ): (
        1,
        "Closing the kitchen removes only an inactive project-owned review gate state; "
        "an active review loop is detected and preserved.",
    ),
    ("execution/session_log.py", "flush_session_log", "shutil.rmtree"): (
        2,
        "The exclusive session-index transaction removes only abandoned summary-less "
        "session directories and expired committed directories selected by retention.",
    ),
    ("core/pipeline_tracker.py", "try_retire_tracker", "target.path.unlink"): (
        1,
        "Exclusive per-tracker lease, tracker lock, strict registry lock, and fresh "
        "authority/liveness reads guard deletion of exactly one tracker JSON.",
    ),
    ("cli/_install_snapshot/_snapshot.py", "_remove", "path.unlink"): (
        1,
        "Transaction restoration removes the failed replacement before restoring its staged copy.",
    ),
    ("cli/_install_snapshot/_snapshot.py", "_remove", "shutil.rmtree"): (
        1,
        "Transaction restoration removes a failed replacement directory under install ownership.",
    ),
    ("cli/_install_snapshot/_snapshot.py", "commit", "shutil.rmtree"): (
        1,
        "The transaction-owned backup is removed only after the installed replacement commits.",
    ),
    ("cli/_install_snapshot/_snapshot.py", "rollback", "shutil.rmtree"): (
        1,
        "Rollback removes its private staging directory after restoring every covered surface.",
    ),
    ("cli/_install_snapshot/_snapshot.py", "stage", "shutil.rmtree"): (
        1,
        "A failed snapshot construction removes only its private transaction staging directory.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_sweep_orphaned_staging",
        "shutil.rmtree",
    ): (
        1,
        "Orphan-staging sweep removes only private staging directories that were "
        "never promoted to a published generation (crash-before-flip debris).",
    ),
    ("core/_plugin_cache.py", "try_reclaim", "record.manifest_path.unlink"): (
        1,
        "The shared retirement engine revalidates the owner-specific exact queued "
        "incarnation before deleting its canonical manifest.",
    ),
    ("core/_plugin_cache.py", "try_reclaim", "os.rename"): (
        1,
        "The shared retirement engine moves an exact incarnation to its private "
        "retry-staging path while holding the owner-specific exclusive lease.",
    ),
    ("core/_plugin_cache.py", "try_reclaim", "shutil.rmtree"): (
        1,
        "The shared retirement engine holds the owner-specific exclusive lease and "
        "revalidates exact identity before removing an artifact tree.",
    ),
    ("workspace/_install_state.py", "reconcile_install_artifacts", "artifact.unlink"): (
        1,
        "The retired-shape registry identifies the exact obsolete install artifact "
        "before reconciliation removes a file or symlink.",
    ),
    ("workspace/_install_state.py", "reconcile_install_artifacts", "shutil.rmtree"): (
        1,
        "The retired-shape registry identifies the exact obsolete install artifact "
        "before reconciliation removes a directory tree.",
    ),
    (
        "workspace/_update_obligation.py",
        "clear_obligation",
        "_obligation_path(home).unlink",
    ): (
        1,
        "Compare-and-delete removes only the matched publication journal while holding "
        "its exclusive obligation lease after exact publication verification.",
    ),
    (
        "workspace/_projected_artifact/_hook_repair.py",
        "_rollback_repair",
        "manifest_path.unlink",
    ): (
        1,
        "Rollback removes only a newly-created external manifest while holding the "
        "incarnation's exclusive artifact lease.",
    ),
    ("workspace/session_skills.py", "_remove_and_verify", "shutil.rmtree"): (
        1,
        "Generated session homes are ephemeral lease-owned artifacts, and cleanup "
        "refuses symlinks before recursively removing the exact requested home.",
    ),
    ("workspace/session_skills.py", "resolve_ephemeral_root", "probe.unlink"): (
        1,
        "The writable-root probe removes only the sentinel file it created in the "
        "candidate ephemeral session-artifact directory.",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "_publish_projected_plugin_manifest",
        "os.replace",
    ): (
        1,
        "Named publication seam called only while the projection authority owns LOCK_EX.",
    ),
    (
        "workspace/_projected_artifact/materialization.py",
        "_replace_directory",
        "destination.unlink",
    ): (
        1,
        "Named root-publication seam called only while the projection authority owns LOCK_EX.",
    ),
    (
        "workspace/_projected_artifact/materialization.py",
        "_replace_directory",
        "os.replace",
    ): (
        1,
        "Named root-publication seam called only while the projection authority owns LOCK_EX.",
    ),
    (
        "workspace/_projected_artifact/materialization.py",
        "_replace_directory",
        "shutil.rmtree",
    ): (
        1,
        "Named root-publication seam called only while the projection authority owns LOCK_EX.",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "_stage_projected_plugin_artifact",
        "shutil.rmtree",
    ): (
        1,
        "Failure cleanup removes a private unpublished staging directory.",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "_discard_staging_manifest",
        "manifest.unlink",
    ): (
        1,
        "The shared best-effort cleanup seam removes only a private unpublished "
        "staging manifest and preserves any active publication exception.",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "acquire_launch_binding",
        "shutil.rmtree",
    ): (
        1,
        "Post-publication cleanup removes the private staging root, never the public root.",
    ),
    (
        "workspace/_projected_artifact/materialization.py",
        "materialize_agent_skill_tree",
        "shutil.rmtree",
    ): (
        1,
        "Non-plugin session-tree staging cleanup is outside the managed projection root.",
    ),
    (
        "workspace/_projected_artifact/materialization.py",
        "materialize_sanitized_plugin_root",
        "shutil.rmtree",
    ): (
        1,
        "Non-public staging cleanup precedes artifact publication.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_replace_symlink",
        "temporary.unlink",
    ): (
        1,
        "Clears a leftover process-private temp symlink candidate (PID-scoped name) "
        "before staging the atomic generation-selector flip.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_replace_symlink",
        "os.replace",
    ): (
        1,
        "The sole generation-selector commit point: renames the process-private temp "
        "symlink onto the public selector path under the caller's install lock.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "publish_generation",
        "os.rename",
    ): (
        1,
        "Moves digest-verified staged content from its private staging directory to "
        "its final generation path; the tree is unpublished until this rename.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "publish_generation",
        "shutil.rmtree",
    ): (
        1,
        "Failure cleanup removes only the private, unpublished staging directory "
        "created for this publish attempt.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "publish_generation",
        "selector.unlink",
    ): (
        1,
        "Restores an absent pre-publication selector after a failed atomic flip.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_discard_unpublished_generation",
        "shutil.rmtree",
    ): (
        1,
        "Removes only the exact fresh generation that failed before selector commitment.",
    ),
    (
        "workspace/_projected_artifact/_generation_publication.py",
        "_discard_unpublished_generation",
        "path.unlink",
    ): (
        1,
        "Removes the manifest and lease sidecars for the exact unpublished generation.",
    ),
    (
        "execution/backends/codex.py",
        "_atomically_replace_explorer_projection",
        "os.replace",
    ): (
        3,
        "The parent and role projections are swapped as one staged server-owned Codex "
        "session-root transaction with rollback.",
    ),
    (
        "execution/backends/codex.py",
        "_atomically_replace_explorer_projection",
        "shutil.rmtree",
    ): (
        1,
        "Explorer projection staging cleanup removes only a private temporary directory.",
    ),
}

PASS_FDS_ALLOWLIST: dict[tuple[str, str, str], tuple[int, str]] = {
    ("cli/session/_session_cook.py", "cook", "pass_fds"): (
        1,
        "Cook passes the stable merge of command, home, and attempt descriptors.",
    ),
    ("cli/session/_session_launch.py", "_run_interactive_session", "spec.inherited_fds"): (
        1,
        "Direct interactive launch forwards the command-derived descriptor tuple exactly.",
    ),
    ("cli/session/_session_process.py", "run_cook_attempt", "inherited_fds"): (
        1,
        "Direct PTY-free cook launch forwards the normalized owned descriptor tuple.",
    ),
    ("cli/session/_session_process.py", "run_cook_attempt", "launcher_fds"): (
        1,
        "PTY launch adds only the slave descriptor to the owned descriptor tuple.",
    ),
    (
        "execution/backends/_codex_session_storage.py",
        "__enter__",
        "tuple((fd for fd in pass_fds if fd >= 0))",
    ): (
        1,
        "Generated Codex home construction forwards its independent storage leases.",
    ),
    ("execution/backends/claude.py", "cook_session_context", "()"): (
        1,
        "The context probe is not a physical artifact-consuming agent launch.",
    ),
    (
        "execution/headless/_headless_launch.py",
        "_run_headless_attempt",
        "spec.inherited_fds",
    ): (
        1,
        "Each provider attempt forwards its freshly built command descriptor tuple.",
    ),
    (
        "execution/headless/_headless_launch.py",
        "_attempt_contract_nudge",
        "spec.inherited_fds",
    ): (
        1,
        "Contract nudge forwards the freshly acquired binding through its rebuilt command.",
    ),
    ("execution/process/__init__.py", "__call__", "pass_fds"): (
        1,
        "The generic subprocess runner forwards its protocol-owned descriptor tuple.",
    ),
    ("execution/process/__init__.py", "run_managed_async", "_inherited_fds"): (
        1,
        "The physical anyio spawn receives the normalized generic runner tuple.",
    ),
    ("execution/recording.py", "__call__", "pass_fds"): (
        3,
        "Recording delegates physical spawns to FD-aware inner runners without "
        "dropping ownership.",
    ),
    ("execution/recording.py", "_record_non_pty_session", "pass_fds"): (
        1,
        "The recording helper forwards ownership to its FD-aware physical runner.",
    ),
    ("workspace/session_skills.py", "managed_session", "(lease_fd,)"): (
        1,
        "The generated-home helper forwards its independent session-storage lease.",
    ),
}

STRICT_PLUGIN_WRITE_ALLOWLIST: dict[tuple[str, str, str], tuple[int, str]] = {
    (
        "core/_plugin_cache.py",
        "_write_retiring_cache_unlocked",
        "write_versioned_json:strict=True",
    ): (
        1,
        "Every retirement-v2 migration and mutation surfaces file and parent fsync failures.",
    ),
    (
        "workspace/_installed_artifact.py",
        "write_installed_plugin_artifact_manifest_locked",
        "write_versioned_json:strict=True",
    ): (
        1,
        "Installed incarnation publication persists exact identity before launch; "
        "shared by first publish and the in-process repair's manifest refresh.",
    ),
    (
        "workspace/_projected_artifact/authority.py",
        "_stage_projected_plugin_artifact",
        "write_versioned_json:strict=True",
    ): (
        1,
        "The staged projection manifest is durable before public root publication.",
    ),
}

_PLUGIN_LIFECYCLE_SYMBOLS = frozenset(
    {
        "ArtifactLease",
        "InstalledPluginArtifactRetirementOwner",
        "_InstallSnapshot",
        "PluginArtifactIdentity",
        "PluginArtifactKind",
        "PluginArtifactRetirementOwner",
        "PluginArtifactValidationError",
        "PluginLaunchBinding",
        "ProjectedPluginRetirementOwner",
        "RETIRED_INSTALL_ARTIFACT_SHAPES",
        "RetiringArtifactRecord",
        "read_retiring_cache",
    }
)
_STRICT_PLUGIN_WRITE_SYMBOLS = frozenset(
    {
        "LegacyRetiringEvidence",
        "PluginArtifactIdentity",
        "RetiringArtifactRecord",
    }
)


def _iter_src_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


class _ScopedCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope = "<module>"
        self.calls: list[tuple[str, ast.Call]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.scope
        self.scope = node.name
        self.generic_visit(node)
        self.scope = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append((self.scope, node))
        self.generic_visit(node)


def _scoped_calls(tree: ast.AST) -> tuple[tuple[str, ast.Call], ...]:
    visitor = _ScopedCallVisitor()
    visitor.visit(tree)
    return tuple(visitor.calls)


def _assert_inventory(
    *,
    actual: Counter[tuple[str, str, str]],
    expected: dict[tuple[str, str, str], tuple[int, str]],
    label: str,
) -> None:
    assert expected, f"{label} inventory must not be empty"
    expected_counts = Counter({key: count for key, (count, _rationale) in expected.items()})
    assert actual == expected_counts, (
        f"{label} inventory drifted.\n"
        f"Added or count-changed: {actual - expected_counts}\n"
        f"Stale entries: {expected_counts - actual}"
    )
    for key, (_count, rationale) in expected.items():
        assert (SRC_ROOT / key[0]).is_file(), f"{label} module no longer exists: {key[0]}"
        assert len(rationale) > 40, f"{label} rationale too thin for {key}"


def _call_name(call: ast.Call) -> str:
    return ast.unparse(call.func)


def _referenced_symbols(tree: ast.AST) -> frozenset[str]:
    return frozenset(
        node.id
        if isinstance(node, ast.Name)
        else node.attr
        if isinstance(node, ast.Attribute)
        else node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute, ast.alias))
    )


def _is_plugin_lifecycle_tree(tree: ast.AST) -> bool:
    return not _referenced_symbols(tree).isdisjoint(_PLUGIN_LIFECYCLE_SYMBOLS)


def _strict_plugin_write_scopes(tree: ast.AST) -> frozenset[str]:
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not _referenced_symbols(node).isdisjoint(_STRICT_PLUGIN_WRITE_SYMBOLS)
    )


def _scan_plugin_mutations_in_tree(
    rel: str,
    tree: ast.AST,
) -> Counter[tuple[str, str, str]]:
    hits: Counter[tuple[str, str, str]] = Counter()
    path_terms = (
        "path",
        "root",
        "dir",
        "manifest",
        "staging",
        "target",
        "backup",
        "destination",
    )
    for scope, call in _scoped_calls(tree):
        name = _call_name(call)
        operation = name.rsplit(".", 1)[-1]
        destructive = operation in {"unlink", "rename", "rmtree", "move"}
        destructive = destructive or (
            operation == "replace"
            and (name == "os.replace" or any(term in name.lower() for term in path_terms))
        )
        if destructive:
            hits[(rel, scope, name)] += 1
    return hits


def _scan_plugin_mutation_trees(
    sources: Iterable[tuple[str, ast.AST]],
) -> Counter[tuple[str, str, str]]:
    hits: Counter[tuple[str, str, str]] = Counter()
    for rel, tree in sources:
        is_projected_artifact_module = rel.startswith("workspace/_projected_artifact/")
        if not is_projected_artifact_module and not _is_plugin_lifecycle_tree(tree):
            continue
        hits.update(_scan_plugin_mutations_in_tree(rel, tree))
    return hits


def _scan_plugin_mutations() -> Counter[tuple[str, str, str]]:
    return _scan_plugin_mutation_trees(
        (_rel(path), ast.parse(path.read_text())) for path in _iter_src_files()
    )


def _scan_pass_fds_in_tree(
    rel: str,
    tree: ast.AST,
) -> Counter[tuple[str, str, str]]:
    hits: Counter[tuple[str, str, str]] = Counter()
    for scope, call in _scoped_calls(tree):
        for keyword in call.keywords:
            if keyword.arg == "pass_fds":
                hits[(rel, scope, ast.unparse(keyword.value))] += 1
    return hits


def _scan_pass_fds() -> Counter[tuple[str, str, str]]:
    hits: Counter[tuple[str, str, str]] = Counter()
    for path in _iter_src_files():
        rel = _rel(path)
        hits.update(_scan_pass_fds_in_tree(rel, ast.parse(path.read_text())))
    return hits


def _scan_strict_plugin_writes_in_tree(
    rel: str,
    tree: ast.AST,
    scopes: frozenset[str],
) -> Counter[tuple[str, str, str]]:
    hits: Counter[tuple[str, str, str]] = Counter()
    for scope, call in _scoped_calls(tree):
        if scope not in scopes:
            continue
        call_name = _call_name(call)
        if call_name not in {"atomic_write", "write_versioned_json"}:
            continue
        strict = next(
            (
                ast.unparse(keyword.value)
                for keyword in call.keywords
                if keyword.arg == "strict_durability"
            ),
            "<missing>",
        )
        hits[(rel, scope, f"{call_name}:strict={strict}")] += 1
    return hits


def _scan_strict_plugin_write_trees(
    sources: Iterable[tuple[str, ast.AST]],
) -> Counter[tuple[str, str, str]]:
    hits: Counter[tuple[str, str, str]] = Counter()
    for rel, tree in sources:
        scopes = _strict_plugin_write_scopes(tree)
        if not scopes:
            continue
        hits.update(
            _scan_strict_plugin_writes_in_tree(
                rel,
                tree,
                scopes,
            )
        )
    return hits


def _scan_strict_plugin_writes() -> Counter[tuple[str, str, str]]:
    return _scan_strict_plugin_write_trees(
        (_rel(path), ast.parse(path.read_text())) for path in _iter_src_files()
    )


def _scan_caller_grace(tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            if any("grace" in arg.arg or "max_defer" in arg.arg for arg in args):
                if node.name in {"due_retiring_records", "sweep_due", "try_reclaim"}:
                    hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            call_name = _call_name(node).rsplit(".", 1)[-1]
            if call_name in {"due_retiring_records", "sweep_due", "try_reclaim"} and any(
                keyword.arg is not None and ("grace" in keyword.arg or "max_defer" in keyword.arg)
                for keyword in node.keywords
            ):
                hits.append(node.lineno)
    return hits


def _scan_dropped_cmdspec_fds(tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if name != "CmdSpec":
            continue
        rendered_values = " ".join(
            ast.unparse(value)
            for value in (
                *node.args,
                *(keyword.value for keyword in node.keywords),
            )
        )
        reconstructs = "spec.cmd" in rendered_values or "spec.env" in rendered_values
        if reconstructs and not any(keyword.arg == "inherited_fds" for keyword in node.keywords):
            hits.append(node.lineno)
    return hits


def _scan_registry_readers(tree: ast.AST) -> list[int]:
    """Lines calling registered_install_paths / naming installPath."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name in {"registered_install_paths", "_get_autoskillit_install_path"}:
                hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value == "installPath":
            hits.append(node.lineno)
    return hits


def _scan_destination_resolve(tree: ast.AST) -> list[int]:
    """Lines assigning `<something>.resolve()` to a `*destination*` name.

    The precise shape of the original defect: `resolved_destination =
    destination.resolve()`, then a containment test against the source root.
    """
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any("destination" in t for t in targets):
            continue
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "resolve"
        ):
            hits.append(node.lineno)
    return hits


def _scan_legacy_plugin_source(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            arg.arg == "plugin_source"
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        ):
            hits.append((node.lineno, "parameter"))
        elif isinstance(node, ast.Attribute) and node.attr == "plugin_source":
            hits.append((node.lineno, "attribute"))
        elif isinstance(node, ast.keyword) and node.arg == "plugin_source":
            hits.append((node.lineno, "keyword"))
    return hits


class TestNoHandRolledRegistryResolution:
    def test_only_allowlisted_modules_read_the_plugin_registry(self) -> None:
        violations: list[str] = []
        for path in _iter_src_files():
            rel = _rel(path)
            if rel in REGISTRY_READ_ALLOWLIST:
                continue
            for line in _scan_registry_readers(ast.parse(path.read_text())):
                violations.append(f"{rel}:{line}")
        assert not violations, (
            "installed_plugins.json is written, versioned, and garbage-collected by "
            "Claude Code — a path read from it can name a directory that no longer "
            "exists. Construct lazy artifact authority from pkg_root() via "
            "project_default_plugin_authority(). If a new module genuinely needs to "
            "*report* on the registry, add it to REGISTRY_READ_ALLOWLIST with a "
            f"rationale.\nViolations: {violations}"
        )

    def test_allowlist_entries_exist_and_carry_rationales(self) -> None:
        """Prevents allowlist rot: a stale entry silently widens the ratchet."""
        for rel, rationale in REGISTRY_READ_ALLOWLIST.items():
            path = SRC_ROOT / rel
            assert path.is_file(), f"allowlisted module no longer exists: {rel}"
            assert len(rationale) > 40, f"{rel}: rationale too thin"
            assert _scan_registry_readers(ast.parse(path.read_text())), (
                f"stale registry reader allowlist entry: {rel}"
            )

    def test_ratchet_fails_on_an_injected_violation(self, tmp_path: Path) -> None:
        """Meta-test: proves the scanner has teeth."""
        injected = tmp_path / "injected.py"
        injected.write_text("entry = data['plugins'][key]['installPath']\n")
        assert _scan_registry_readers(ast.parse(injected.read_text()))


class TestNoRawResolveInContainmentChecks:
    def test_write_destinations_use_the_shared_primitive(self) -> None:
        violations: list[str] = []
        for path in _iter_src_files():
            rel = _rel(path)
            if rel in DESTINATION_RESOLVE_ALLOWLIST:
                continue
            for line in _scan_destination_resolve(ast.parse(path.read_text())):
                violations.append(f"{rel}:{line}")
        assert not violations, (
            "Path.resolve() follows a final-component symlink, so on a write "
            "destination it answers 'what does this point at?' instead of 'where am "
            "I about to write?'. Use destination_location() from core.paths.\n"
            f"Violations: {violations}"
        )

    def test_allowlist_entries_exist_and_carry_rationales(self) -> None:
        for rel, rationale in DESTINATION_RESOLVE_ALLOWLIST.items():
            assert (SRC_ROOT / rel).is_file(), f"allowlisted module no longer exists: {rel}"
            assert len(rationale) > 20, f"{rel}: rationale too thin"

    def test_ratchet_fails_on_an_injected_violation(self, tmp_path: Path) -> None:
        injected = tmp_path / "injected.py"
        injected.write_text("resolved_destination = destination.resolve()\n")
        assert _scan_destination_resolve(ast.parse(injected.read_text()))


class TestNoLegacyPluginSourceContext:
    """A context may carry lazy authority, never a durable bare plugin path."""

    def test_source_has_no_plugin_source_parameter_or_attribute(self) -> None:
        violations: list[str] = []
        for path in _iter_src_files():
            rel = _rel(path)
            for line, kind in _scan_legacy_plugin_source(ast.parse(path.read_text())):
                violations.append(f"{rel}:{line}:{kind}")
        assert not violations, (
            "plugin_source stores a bare path beyond a launch lifetime; carry "
            "plugin_authority on contexts and plugin_binding at builders.\n"
            f"Violations: {violations}"
        )

    def test_ratchet_detects_injected_raw_context_path(self) -> None:
        injected = ast.parse("def launch(plugin_source):\n    return context.plugin_source\n")
        assert _scan_legacy_plugin_source(injected)


class TestEveryPluginDirEmitterIsBindingDerived:
    """Every builder classifies plugin paths as launch-binding-derived."""

    def test_all_builders_accept_bindings_not_raw_paths(self) -> None:
        import inspect

        from autoskillit.execution.backends import BACKEND_REGISTRY

        checked: list[str] = []

        for backend_cls in BACKEND_REGISTRY.values():
            backend = backend_cls()
            for name, method in inspect.getmembers(backend, inspect.ismethod):
                if not name.startswith("build_") or not name.endswith("_cmd"):
                    continue
                params = inspect.signature(method).parameters
                assert "plugin_source" not in params, (
                    f"{backend.name}.{name} still accepts the legacy bare-path context"
                )
                assert "plugin_dir" not in params, (
                    f"{backend.name}.{name} accepts a raw plugin_dir instead of a binding"
                )
                if "plugin_binding" not in params:
                    continue
                annotation = str(params["plugin_binding"].annotation)
                assert "PluginLaunchBinding" in annotation, (
                    f"{backend.name}.{name} does not classify its plugin path as "
                    f"binding-derived: {annotation}"
                )
                checked.append(f"{backend.name}.{name}")

        assert checked, "reflection found no --plugin-dir emitting builders — scan is broken"


class TestPluginMutationInventory:
    def test_every_plugin_mutation_is_classified(self) -> None:
        _assert_inventory(
            actual=_scan_plugin_mutations(),
            expected=PLUGIN_MUTATION_ALLOWLIST,
            label="plugin mutation",
        )

    def test_sidecar_deletion_is_impossible(self) -> None:
        sidecar_mutations = [
            key
            for key in _scan_plugin_mutations()
            if "lease" in key[2].lower() or "lock" in key[2].lower()
        ]
        assert not sidecar_mutations, (
            "Artifact lease sidecars are durable synchronization identities and "
            f"must never be deleted: {sidecar_mutations}"
        )

    def test_every_lifecycle_persistence_write_is_strict(self) -> None:
        _assert_inventory(
            actual=_scan_strict_plugin_writes(),
            expected=STRICT_PLUGIN_WRITE_ALLOWLIST,
            label="strict plugin write",
        )

    def test_ratchet_fails_on_injected_unclassified_mutation(self) -> None:
        injected = ast.parse("def mutate(destination):\n    destination.replace(other)\n")
        actual = _scan_plugin_mutations()
        actual.update(_scan_plugin_mutations_in_tree("injected.py", injected))
        with pytest.raises(AssertionError, match="inventory drifted"):
            _assert_inventory(
                actual=actual,
                expected=PLUGIN_MUTATION_ALLOWLIST,
                label="plugin mutation",
            )

    def test_sidecar_ratchet_detects_injected_unlink(self) -> None:
        injected = ast.parse("def mutate(lease_path):\n    lease_path.unlink()\n")
        hits = _scan_plugin_mutations_in_tree("injected.py", injected)
        assert any("lease" in key[2] for key in hits)

    def test_strict_write_ratchet_detects_missing_durability(self) -> None:
        injected = ast.parse(
            "def publish(identity: PluginArtifactIdentity):\n"
            "    write_versioned_json(identity.manifest_path, payload)\n"
        )
        hits = _scan_strict_plugin_write_trees([("new_plugin_owner.py", injected)])
        assert hits == Counter(
            {
                (
                    "new_plugin_owner.py",
                    "publish",
                    "write_versioned_json:strict=<missing>",
                ): 1
            }
        )

    @pytest.mark.parametrize(
        "lifecycle_symbol",
        ["ArtifactLease", "PluginLaunchBinding", "RetiringArtifactRecord"],
    )
    def test_mutation_ratchet_discovers_new_lifecycle_module(
        self,
        lifecycle_symbol: str,
    ) -> None:
        injected = ast.parse(
            f"def reclaim(record: {lifecycle_symbol}):\n    record.manifest_path.unlink()\n"
        )
        hits = _scan_plugin_mutation_trees([("new_plugin_owner.py", injected)])
        assert hits == Counter(
            {("new_plugin_owner.py", "reclaim", "record.manifest_path.unlink"): 1}
        )


class TestRetirementPolicyOwnership:
    def test_v2_callers_cannot_supply_grace_or_max_defer(self) -> None:
        violations: list[str] = []
        for path in _iter_src_files():
            for line in _scan_caller_grace(ast.parse(path.read_text())):
                violations.append(f"{_rel(path)}:{line}")
        assert not violations, (
            "Retirement records own their absolute not_before policy; callers may "
            f"not reinterpret it with grace or max-defer arguments: {violations}"
        )

    def test_ratchet_detects_injected_caller_grace(self) -> None:
        injected = ast.parse("coordinator.sweep_due(now, grace_hours=2)\n")
        assert _scan_caller_grace(injected)


class TestInheritedFDInventory:
    def test_every_pass_fds_site_is_classified(self) -> None:
        _assert_inventory(
            actual=_scan_pass_fds(),
            expected=PASS_FDS_ALLOWLIST,
            label="pass_fds",
        )

    def test_pass_fds_never_reconstructs_ownership_from_a_path(self) -> None:
        forbidden = ("plugin_dir", "plugin_source", "managed_path", "manifest_path")
        violations = [key for key in _scan_pass_fds() if any(term in key[2] for term in forbidden)]
        assert not violations, (
            "Descriptor ownership must come from CmdSpec.inherited_fds or another "
            f"owned lease, never a reconstructed path: {violations}"
        )

    def test_cmdspec_reconstruction_preserves_inherited_fds(self) -> None:
        violations: list[str] = []
        for path in _iter_src_files():
            for line in _scan_dropped_cmdspec_fds(ast.parse(path.read_text())):
                violations.append(f"{_rel(path)}:{line}")
        assert not violations, (
            "Every CmdSpec reconstructed from an existing spec must preserve "
            f"inherited_fds: {violations}"
        )

    def test_inventory_and_reconstruction_ratchets_detect_injected_violations(self) -> None:
        injected_pass = ast.parse(
            "def spawn():\n    subprocess.Popen(cmd, pass_fds=plugin_dir.fds)\n"
        )
        actual = _scan_pass_fds()
        actual.update(_scan_pass_fds_in_tree("injected.py", injected_pass))
        with pytest.raises(AssertionError, match="inventory drifted"):
            _assert_inventory(
                actual=actual,
                expected=PASS_FDS_ALLOWLIST,
                label="pass_fds",
            )

        injected_spec = ast.parse(
            "def rebuild(spec):\n    return CmdSpec(cmd=spec.cmd, env=spec.env)\n"
        )
        assert _scan_dropped_cmdspec_fds(injected_spec)
