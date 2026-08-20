"""Retirement owners must declare and wire every destructive-reclaim backstop."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoskillit.core import (
    RETIREMENT_BACKSTOP_LEDGER,
    PluginArtifactKind,
    PluginLoadMode,
    RetirementOutcome,
    read_retiring_cache,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import (
    ProjectedPluginArtifactAuthority,
    ProjectedPluginRetirementOwner,
    project_default_plugin_authority,
)
from tests.contracts._projection_helpers import session_catalog

pytestmark = pytest.mark.medium

_PROJECT_ROOT = Path(__file__).parents[2]
_SOURCE_ROOT = _PROJECT_ROOT / "src"
_ENGINE_NAME = "PluginArtifactRetirementEngine"


def _authority(tmp_path: Path) -> ProjectedPluginArtifactAuthority:
    return project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=session_catalog(),
    )


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix is not None else None
    return None


class _EngineImportMap:
    """Resolve direct and module-qualified imports of the retirement engine."""

    def __init__(self, tree: ast.Module) -> None:
        self._direct_names: set[str] = set()
        self._module_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith("autoskillit.core"):
                    self._direct_names.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == _ENGINE_NAME
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("autoskillit.core"):
                        self._module_names.add(alias.asname or alias.name)

    def resolves_engine(self, call: ast.Call) -> bool:
        if isinstance(call.func, ast.Name):
            return call.func.id in self._direct_names
        if not isinstance(call.func, ast.Attribute) or call.func.attr != _ENGINE_NAME:
            return False
        module_name = _dotted_name(call.func.value)
        return module_name in self._module_names


def _engine_calls(tree: ast.Module) -> list[ast.Call]:
    imports = _EngineImportMap(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and imports.resolves_engine(node)
    ]


class _OwnerDiscovery(ast.NodeVisitor):
    def __init__(self, path: Path, tree: ast.Module) -> None:
        self._path = path
        self._imports = _EngineImportMap(tree)
        self._class_names: list[str] = []
        self.owners: set[tuple[PluginArtifactKind, str]] = set()
        self.unresolved: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_names.append(node.name)
        for child in node.body:
            self.visit(child)
        self._class_names.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self._imports.resolves_engine(node) and self._class_names:
            artifact_kind = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "artifact_kind"),
                None,
            )
            if not (
                isinstance(artifact_kind, ast.Attribute)
                and isinstance(artifact_kind.value, ast.Name)
                and artifact_kind.value.id == "PluginArtifactKind"
            ):
                location = self._path.relative_to(_PROJECT_ROOT)
                self.unresolved.append(f"{location}:{node.lineno}")
            else:
                try:
                    kind = PluginArtifactKind[artifact_kind.attr]
                except KeyError:
                    location = self._path.relative_to(_PROJECT_ROOT)
                    self.unresolved.append(f"{location}:{node.lineno}")
                else:
                    self.owners.add((kind, ".".join(self._class_names)))
        self.generic_visit(node)


def _discover_engine_owners() -> tuple[set[tuple[PluginArtifactKind, str]], list[str]]:
    owners: set[tuple[PluginArtifactKind, str]] = set()
    unresolved: list[str] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _OwnerDiscovery(path, tree)
        visitor.visit(tree)
        owners.update(visitor.owners)
        unresolved.extend(visitor.unresolved)
    return owners, unresolved


def test_every_owner_declares_its_reclaim_backstops() -> None:
    discovered, unresolved = _discover_engine_owners()
    assert not unresolved, (
        "owner engine constructions must pass artifact_kind as a direct "
        f"PluginArtifactKind member: {sorted(unresolved)}"
    )
    declared = {
        (backstop.artifact_kind, backstop.owner_qualname)
        for backstop in RETIREMENT_BACKSTOP_LEDGER.values()
    }

    missing = sorted(discovered - declared, key=lambda item: (item[0].value, item[1]))
    assert not missing, f"retirement owners missing ledger declarations: {missing}"

    removed = sorted(declared - discovered, key=lambda item: (item[0].value, item[1]))
    assert not removed, f"ledger declarations without a live retirement owner: {removed}"


def test_every_engine_construction_passes_is_current_explicitly() -> None:
    missing: list[str] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _engine_calls(tree):
            if not any(keyword.arg == "is_current" for keyword in call.keywords):
                location = path.relative_to(_PROJECT_ROOT)
                missing.append(f"{location}:{call.lineno}")

    assert not missing, (
        "every PluginArtifactRetirementEngine construction must pass is_current "
        f"explicitly: {missing}"
    )


def test_a_held_launch_lease_blocks_reclaim_of_the_bound_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binding = _authority(tmp_path).acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        assert binding.plugin_dir is not None
        deadline = datetime.now(UTC)
        owner = ProjectedPluginRetirementOwner(binding.plugin_dir.parent)
        append_result = owner.enqueue_retirement(binding.identity, deadline)
        assert append_result is not None
        record = next(
            item
            for item in read_retiring_cache().records
            if item.record_id == append_result.record_id
        )

        assert owner.try_reclaim(record, deadline + timedelta(seconds=1)) is (
            RetirementOutcome.DEFERRED_CONTENDED
        )
        assert binding.plugin_dir.is_dir()
    finally:
        binding.close()


def test_reclaim_is_blocked_when_cancel_could_not_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli.install._plugin_artifact import (
        default_plugin_retirement_coordinator,
    )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    initial = authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    identity = initial.identity
    initial.close()

    deadline = datetime.now(UTC)
    owner = ProjectedPluginRetirementOwner(identity.managed_path.parent)
    append_result = owner.enqueue_retirement(identity, deadline)
    assert append_result is not None
    cache = tmp_path / ".autoskillit" / "retiring_cache.json"
    exact_payload = json.loads(cache.read_text(encoding="utf-8"))
    corrupt_payload = {**exact_payload, "unexpected_authority": True}
    cache.write_text(json.dumps(corrupt_payload), encoding="utf-8")

    binding = authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        assert binding.plugin_dir is not None
        cache.write_text(json.dumps(exact_payload), encoding="utf-8")
        assert append_result.record_id in {
            record.record_id for record in read_retiring_cache().records
        }

        outcomes = default_plugin_retirement_coordinator().sweep_due(
            deadline + timedelta(seconds=1)
        )

        assert RetirementOutcome.DEFERRED_CONTENDED in outcomes
        assert binding.plugin_dir.is_dir()
    finally:
        binding.close()
