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
from pathlib import Path

import pytest

pytestmark = [pytest.mark.medium]

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

#: Modules permitted to read a plugin root out of `installed_plugins.json`.
#: There is exactly one, and it is a *reporting* primitive — no execution path
#: may derive a plugin source from it.
REGISTRY_READ_ALLOWLIST: dict[str, str] = {
    "core/_plugin_ids.py": (
        "Defines registered_install_paths() itself — the diagnostics-only reader. "
        "Its docstring states the constraint that no resolution path may use it."
    ),
    "workspace/_install_state.py": (
        "verify_install_state() reports registry/filesystem disagreement. Reporting a "
        "dangling installPath is the point; nothing here resolves a source from it."
    ),
    "cli/doctor/_doctor_mcp.py": (
        "The doctor check that dereferences installPath. Returning OK for a merely "
        "present key is the inversion this ratchet exists to prevent recurring."
    ),
    "core/_plugin_cache.py": (
        "sweep_retiring_cache consults the registry so it never deletes a directory "
        "the registry still names. A consult, not a resolution."
    ),
}

#: Modules permitted to call `.resolve()` on a *write destination* before a
#: containment comparison. Only the shared primitive.
DESTINATION_RESOLVE_ALLOWLIST: dict[str, str] = {
    "core/paths.py": "Defines destination_location(), the shared primitive itself.",
}


def _iter_src_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


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
            assert (SRC_ROOT / rel).is_file(), f"allowlisted module no longer exists: {rel}"
            assert len(rationale) > 40, f"{rel}: rationale too thin"

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
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    arg.arg == "plugin_source"
                    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                ):
                    violations.append(f"{rel}:{node.lineno}:parameter")
                elif isinstance(node, ast.Attribute) and node.attr == "plugin_source":
                    violations.append(f"{rel}:{node.lineno}:attribute")
                elif isinstance(node, ast.keyword) and node.arg == "plugin_source":
                    violations.append(f"{rel}:{node.lineno}")
        assert not violations, (
            "plugin_source stores a bare path beyond a launch lifetime; carry "
            "plugin_authority on contexts and plugin_binding at builders.\n"
            f"Violations: {violations}"
        )


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
