"""A materialized managed Codex home stays verifiable through launch preparation."""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.cli.session._session_launch import (
    _finalize_interactive_launch,
    prepare_interactive_launch,
)
from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    CODEX_MODEL_ALIASES,
    FreshLaunch,
    ManagedSessionHome,
    SemanticAdaptationContext,
    SkillExecutionRole,
    SkillSource,
    pkg_root,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.backends._codex_config import _serialize_toml
from autoskillit.server._managed_join_attestation import (
    DefaultManagedJoinAttestationAuthority,
    ManagedJoinRecordStore,
)
from autoskillit.server.managed_join_prelaunch import prepare_managed_join_context
from autoskillit.workspace import (
    DefaultSessionSkillManager,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillInfo,
    SkillsDirectoryProvider,
    compile_session_skill_catalog,
)
from tests.cli.test_session_launch import _write_codex_mcp_probe_executable
from tests.execution.backends._codex_fixtures import (
    generated_home_snapshot,
    installed_catalog,
    managed_source_home,
    use_bundled_catalog,
    with_migration_offer,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

ManagedLaunchCase = tuple[CodexBackend, ManagedSessionHome, SemanticAdaptationContext, Path, Path]


@pytest.fixture
def managed_launch_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ManagedLaunchCase]:
    selected = CODEX_MODEL_ALIASES["haiku"]
    source_home, raw_catalog = managed_source_home(
        tmp_path,
        catalog=with_migration_offer(installed_catalog(), selected, f"{selected}-successor"),
    )
    original_which = shutil.which
    use_bundled_catalog(monkeypatch, raw_catalog)
    project = tmp_path / "project"
    project.mkdir()
    backend = CodexBackend(source_codex_home=source_home)
    context = prepare_managed_join_context(
        backend=backend,
        configured_model="haiku",
        state_root=project,
        parent_id="launch1",
        launch_context="interactive",
    )
    assert isinstance(context, SemanticAdaptationContext)

    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    shim = binary_dir / "codex"
    _write_codex_mcp_probe_executable(shim)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(shutil, "which", original_which)

    skill_path = project / "skills" / "launch-skill" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_content = (
        "---\nname: launch-skill\ndescription: Test managed launch.\n---\nRun directly.\n"
    )
    skill_path.write_text(skill_content, encoding="utf-8")
    catalog = EffectiveSkillCatalog(
        (
            SkillCatalogEntry.from_skill_info(
                SkillInfo(
                    name="launch-skill",
                    source=SkillSource.PROJECT_LOCAL,
                    path=skill_path,
                    canonical_content=skill_content,
                )
            ),
        ),
        execution_role=SkillExecutionRole.SESSION,
    )
    provider = SkillsDirectoryProvider()
    projection_context = provider.catalog_projection_context(
        catalog,
        project,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )
    projection_context = replace(
        projection_context,
        adaptation_context=context,
        managed_codex_route="interactive-parent",
    )
    compilation = compile_session_skill_catalog(catalog, backend, adaptation_context=context)
    manager = DefaultSessionSkillManager(
        provider,
        ephemeral_root=tmp_path / "ephemeral",
        persistent_roots={"codex": tmp_path / "persistent" / "codex-sessions"},
    )
    with manager.managed_session("launch1", compilation, projection_context) as managed:
        assert managed.managed_projection is not None
        yield backend, managed, context, project, source_home


def _prepare(
    backend: CodexBackend,
    managed: ManagedSessionHome,
    project: Path,
    *,
    finalizer: bool = False,
) -> None:
    if finalizer:
        _finalize_interactive_launch(
            backend,
            exact_binding_probe_required=backend.capabilities.cook_exact_binding_probe_required,
            project_dir=project,
            extra_env={"PATH": os.environ["PATH"]},
            required_env=None,
            plugin_binding=None,
            launch=FreshLaunch(initial_prompt="hello"),
            add_dirs=(managed.skills_dir,),
            managed_home=managed,
        )
    else:
        prepare_interactive_launch(
            backend,
            project_dir=project,
            extra_env={"PATH": os.environ["PATH"]},
            required_env=None,
            plugin_binding=None,
            launch=FreshLaunch(initial_prompt="hello"),
            add_dirs=(managed.skills_dir,),
            managed_home=managed,
        )


@pytest.mark.parametrize("finalizer", (False, True), ids=("cook", "order-and-fleet"))
def test_launch_preparation_leaves_projected_home_unchanged(
    managed_launch_case: ManagedLaunchCase,
    finalizer: bool,
) -> None:
    backend, managed, _, project, _ = managed_launch_case
    before = generated_home_snapshot(managed.generated_home)

    _prepare(backend, managed, project, finalizer=finalizer)

    assert generated_home_snapshot(managed.generated_home) == before


def test_launched_home_verifies_on_the_server(
    managed_launch_case: ManagedLaunchCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, managed, context, project, source_home = managed_launch_case
    _prepare(backend, managed, project)
    monkeypatch.setenv(CODEX_HOME_ENV_VAR, str(managed.generated_home))
    authority = DefaultManagedJoinAttestationAuthority(
        record_store=ManagedJoinRecordStore(project),
        backend=CodexBackend(source_codex_home=source_home),
    )

    assert isinstance(
        authority.find_verified_context(backend="codex", parent_session_id="launch1"),
        SemanticAdaptationContext,
    )
    assert context.managed_join_attestation is not None
    assert (
        backend.verify_managed_session_dir(
            managed.generated_home, context.managed_join_attestation, "interactive-parent"
        )
        == []
    )


def test_projected_catalog_offers_no_migration(
    managed_launch_case: ManagedLaunchCase,
) -> None:
    _, managed, _, _, _ = managed_launch_case
    catalog = json.loads((managed.generated_home / "autoskillit-models.json").read_text())
    assert all(model.get("upgrade") is None for model in catalog["models"])


@pytest.mark.parametrize(
    ("drift", "expected"),
    (("model", "wrong resolved model"), ("guard", "missing guards: join_stop_guard")),
)
def test_launch_refuses_home_drift_with_specific_reason(
    managed_launch_case: ManagedLaunchCase,
    drift: str,
    expected: str,
) -> None:
    backend, managed, _, project, _ = managed_launch_case
    config_path = managed.generated_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if drift == "model":
        selected = CODEX_MODEL_ALIASES["haiku"]
        other = CODEX_MODEL_ALIASES["sonnet"]
        config["model"] = other
        config.setdefault("notice", {})["model_migrations"] = {selected: other}
    else:
        for entries in config["hooks"].values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                entry["hooks"] = [
                    hook
                    for hook in entry["hooks"]
                    if "join_stop_guard" not in hook.get("command", "")
                ]
    config_path.write_text(_serialize_toml(config), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        _prepare(backend, managed, project)
    assert "managed generated home no longer matches its attestation" in str(exc_info.value)
    assert expected in str(exc_info.value)


def test_foreign_codex_keys_do_not_trip_launch_gate(
    managed_launch_case: ManagedLaunchCase,
) -> None:
    backend, managed, _, project, _ = managed_launch_case
    config_path = managed.generated_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("hooks", {}).setdefault("state", {})["x"] = {"trusted_hash": "foreign"}
    config.setdefault("projects", {})["/p"] = {"trust_level": "trusted"}
    config_path.write_text(_serialize_toml(config), encoding="utf-8")

    _prepare(backend, managed, project)
