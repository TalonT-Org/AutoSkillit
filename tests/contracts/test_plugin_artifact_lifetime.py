"""Projection publication and process-lifetime ownership are one contract."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactContentionError,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    PluginLoadMode,
    RetirementOutcome,
    is_canonical_plugin_artifact_incarnation_id,
    managed_home,
    managed_home_for,
    new_plugin_artifact_incarnation_id,
    read_retiring_cache,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import (
    ProjectedPluginArtifactAuthority,
    project_default_plugin_authority,
    prune_stale_projections,
)
from tests._helpers import _flush_structlog_proxy_caches
from tests.contracts._projection_helpers import session_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _authority(tmp_path: Path) -> ProjectedPluginArtifactAuthority:
    return project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=session_catalog(),
    )


def _semantic_catalog(
    tmp_path: Path,
    declarations: dict[str, str],
):
    from autoskillit.core import SkillExecutionRole, SkillSource
    from autoskillit.workspace import EffectiveSkillCatalog, SkillCatalogEntry
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    entries = []
    plans = {}
    for name, semantic_requirements in declarations.items():
        skill_path = tmp_path / "semantic-skills" / name / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            f"name: {name}\n"
            f"description: {name} semantic test.\n"
            "semantic_version: 1\n"
            "semantic_requirements:\n"
            f"{semantic_requirements}"
            "---\n"
            f"Perform {name}.\n",
            encoding="utf-8",
        )
        info = _skill_info_from_frontmatter(name, SkillSource.PROJECT_LOCAL, skill_path)
        assert info.semantic_plan is not None
        entries.append(SkillCatalogEntry.from_skill_info(info))
        plans[name] = info.semantic_plan
    return (
        EffectiveSkillCatalog(
            skills=tuple(entries),
            execution_role=SkillExecutionRole.SESSION,
        ),
        plans,
    )


def test_authority_creation_is_lazy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    authority = _authority(tmp_path)

    assert authority.catalog is not None
    assert not (tmp_path / ".autoskillit").exists()


@pytest.mark.parametrize(
    ("malformation", "expected"),
    [
        (
            "undeclared_refusal",
            "backend 'claude-code' reported unsupported semantic operation "
            "'child_spawn' not declared by the semantic plan",
        ),
        (
            "incomplete_supported",
            "semantic adaptation omitted observable instructions",
        ),
    ],
)
def test_projected_plugin_propagates_malformed_adapter_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
    expected: str,
) -> None:
    from autoskillit.core import (
        SkillContractError,
        SkillExecutionRole,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
        SkillSource,
    )
    from autoskillit.workspace import EffectiveSkillCatalog, SkillCatalogEntry
    from autoskillit.workspace.skills import _skill_info_from_frontmatter

    skill_path = tmp_path / "projected-semantic-test" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        "---\n"
        "name: projected-semantic-test\n"
        "description: Projected semantic test.\n"
        "semantic_version: 1\n"
        "semantic_requirements:\n"
        "  git_metadata_writes:\n"
        "  - purpose: create one commit\n"
        "---\n"
        "Perform the projected operation.\n",
        encoding="utf-8",
    )
    info = _skill_info_from_frontmatter(
        "projected-semantic-test",
        SkillSource.PROJECT_LOCAL,
        skill_path,
    )
    assert info.semantic_plan is not None
    catalog = EffectiveSkillCatalog(
        skills=(SkillCatalogEntry.from_skill_info(info),),
        execution_role=SkillExecutionRole.SESSION,
    )
    if malformation == "undeclared_refusal":
        result = SkillSemanticAdaptationResult.unsupported(
            backend="claude-code",
            operation=SkillSemanticOperation.CHILD_SPAWN,
        )
    else:
        result = SkillSemanticAdaptationResult()
    monkeypatch.setattr(
        ClaudeCodeBackend,
        "adapt_skill_semantics",
        lambda self, plan, adaptation_context=None: result,
    )

    authority = project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=catalog,
    )
    with pytest.raises(SkillContractError, match=f"^{expected}$"):
        authority._plan(ClaudeCodeBackend())


def test_projected_plugin_plan_retains_mixed_refusal_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import (
        SkillProjectionRefusal,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
    )
    from tests.fakes import adapt_test_skill_semantics

    catalog, plans = _semantic_catalog(
        tmp_path,
        {
            "portable": "  git_metadata_writes:\n  - purpose: create one commit\n",
            "refused": "  join:\n    required: true\n",
        },
    )
    diagnostic = "fixed-set join is unavailable in the projected backend"

    def adapt(_backend, plan, adaptation_context=None):
        if plan is plans["refused"]:
            return SkillSemanticAdaptationResult(
                unsupported_operation=SkillSemanticOperation.REQUIRED_JOIN,
                diagnostic=diagnostic,
            )
        return adapt_test_skill_semantics(plan)

    monkeypatch.setattr(ClaudeCodeBackend, "adapt_skill_semantics", adapt)

    plan = project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=catalog,
    )._plan(ClaudeCodeBackend())

    assert tuple(skill.name for skill in plan.catalog.skills) == ("portable",)
    assert plan.unavailable == (
        SkillProjectionRefusal(
            skill="refused",
            operation=SkillSemanticOperation.REQUIRED_JOIN,
            diagnostic=diagnostic,
        ),
    )


def test_projected_plugin_reuses_supported_adaptation_during_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.workspace._projected_artifact.authority as projection
    from autoskillit.core import (
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
    )

    catalog, _plans = _semantic_catalog(
        tmp_path,
        {
            "stateful": "  git_metadata_writes:\n  - purpose: create one commit\n",
        },
    )
    calls = 0
    supported = SkillSemanticAdaptationResult(
        instruction_fragments=("Use the first admitted adaptation.",),
    )

    def adapt(_backend, _plan, adaptation_context=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return supported
        return SkillSemanticAdaptationResult(
            unsupported_operation=SkillSemanticOperation.GIT_METADATA_WRITE,
            diagnostic="state changed after admission",
        )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ClaudeCodeBackend, "adapt_skill_semantics", adapt)
    authority = project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=catalog,
    )

    plan = authority._plan(ClaudeCodeBackend())
    plan.destination.parent.mkdir(parents=True)
    staged = projection._stage_projected_plugin_artifact(plan)

    assert calls == 1
    assert plan.semantic_adaptations == {"stateful": supported}
    projected = staged.root / "skills" / "stateful" / "SKILL.md"
    assert "Use the first admitted adaptation." in projected.read_text(encoding="utf-8")


def test_all_refused_projection_preserves_previous_publication_and_names_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import (
        PluginArtifactPublicationError,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
    )
    from autoskillit.workspace._projection_cache import projected_plugin_artifact_digest
    from tests.fakes import adapt_test_skill_semantics

    catalog, plans = _semantic_catalog(
        tmp_path,
        {
            "alpha": "  join:\n    required: true\n",
            "beta": (
                "  logical_roles:\n"
                "  - name: worker\n"
                "    purpose: perform one task\n"
                "  child_spawns:\n"
                "  - role: worker\n"
                "    count: 1\n"
            ),
        },
    )
    refusing = False
    diagnostics = {
        "alpha": "alpha cannot use a fixed-set join",
        "beta": "beta cannot spawn a child",
    }
    operations = {
        "alpha": SkillSemanticOperation.REQUIRED_JOIN,
        "beta": SkillSemanticOperation.CHILD_SPAWN,
    }

    def adapt(_backend, plan, adaptation_context=None):
        name = next(name for name, expected in plans.items() if plan is expected)
        if not refusing:
            return adapt_test_skill_semantics(plan)
        return SkillSemanticAdaptationResult(
            unsupported_operation=operations[name],
            diagnostic=diagnostics[name],
        )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ClaudeCodeBackend, "adapt_skill_semantics", adapt)
    authority = project_default_plugin_authority(
        cwd=tmp_path,
        base_branch="main",
        catalog=catalog,
    )
    binding = authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    previous_root = binding.identity.managed_path
    previous_manifest = binding.identity.manifest_path.read_bytes()
    previous_digest = projected_plugin_artifact_digest(previous_root)
    binding.close()
    previous_entries = tuple(sorted(path.name for path in previous_root.parent.iterdir()))
    refusing = True

    with pytest.raises(PluginArtifactPublicationError) as exc_info:
        authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )

    message = str(exc_info.value)
    for name in ("alpha", "beta"):
        assert name in message
        assert operations[name].value in message
        assert diagnostics[name] in message
    assert previous_root.is_dir()
    assert binding.identity.manifest_path.read_bytes() == previous_manifest
    assert projected_plugin_artifact_digest(previous_root) == previous_digest
    assert tuple(sorted(path.name for path in previous_root.parent.iterdir())) == previous_entries


def test_projected_artifact_boundary_is_one_way_and_canonical() -> None:
    import autoskillit.workspace._projected_artifact as projected_artifact
    import autoskillit.workspace._projected_artifact.authority as authority
    import autoskillit.workspace.skill_projection as skill_projection

    assert "autoskillit.workspace.skill_projection" not in inspect.getsource(authority)
    assert (
        skill_projection.ProjectedPluginArtifactAuthority
        is projected_artifact.ProjectedPluginArtifactAuthority
    )
    assert (
        skill_projection.ProjectedPluginRetirementOwner
        is projected_artifact.ProjectedPluginRetirementOwner
    )
    assert (
        skill_projection.materialize_agent_skill_tree
        is projected_artifact.materialize_agent_skill_tree
    )


@pytest.mark.parametrize("invalid", [True, 1.5, 0])
def test_authority_requires_exact_positive_projection_version(
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        project_default_plugin_authority(projection_version=invalid)  # type: ignore[arg-type]


def test_projection_publication_preserves_control_flow_exceptions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import autoskillit.workspace._projected_artifact.authority as projection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def interrupt_publication(_plan):
        raise KeyboardInterrupt("stop projection publication")

    monkeypatch.setattr(
        projection,
        "_stage_projected_plugin_artifact",
        interrupt_publication,
    )

    with pytest.raises(KeyboardInterrupt, match="stop projection publication"):
        _authority(tmp_path).acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )


def test_projection_staging_cleanup_preserves_primary_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from unittest.mock import Mock

    import autoskillit.workspace._projected_artifact.authority as projection

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plan = _authority(tmp_path)._plan(ClaudeCodeBackend())
    plan.destination.parent.mkdir(parents=True)
    logger = Mock()
    monkeypatch.setattr(projection, "logger", logger)

    def fail_after_manifest_write(path: Path, *_args, **_kwargs) -> None:
        path.write_text("staged")
        raise RuntimeError("primary staging failure")

    original_unlink = Path.unlink

    def fail_staging_manifest_unlink(path: Path, *args, **kwargs) -> None:
        if ".manifest-" in path.name:
            raise OSError("cleanup unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(projection, "write_versioned_json", fail_after_manifest_write)
    monkeypatch.setattr(Path, "unlink", fail_staging_manifest_unlink)

    with pytest.raises(RuntimeError, match="primary staging failure"):
        projection._stage_projected_plugin_artifact(plan)

    logger.warning.assert_called_once()
    assert logger.warning.call_args.args == ("projected_plugin_staging_cleanup_failed",)
    assert logger.warning.call_args.kwargs["error"] == "cleanup unlink failure"
    assert ".manifest-" in Path(logger.warning.call_args.kwargs["manifest_path"]).name


def test_binding_owns_exact_v2_incarnation_and_stable_sidecar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()

    first = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    second = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    )
    try:
        assert first.identity == second.identity
        assert first.plugin_dir == first.identity.managed_path
        assert second.plugin_dir == second.identity.managed_path
        assert first.inherited_fds != second.inherited_fds
        assert first.identity.manifest_schema_version == 2
        manifest = json.loads(first.identity.manifest_path.read_text(encoding="utf-8"))
        assert manifest["semantic_key"] == first.identity.semantic_key
        assert manifest["incarnation_id"] == first.identity.incarnation_id
        assert manifest["artifact_digest"] == first.identity.artifact_digest
        assert is_canonical_plugin_artifact_incarnation_id(first.identity.incarnation_id)

        lease_path = (
            first.identity.managed_path.parent
            / ".artifact-leases"
            / f"{first.identity.semantic_key}.lock"
        )
        assert lease_path.is_file()
        assert first.identity.managed_path not in lease_path.parents
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(lease_path, blocking=False)
    finally:
        first.close()
        second.close()


def test_projection_lifecycle_events_cover_publication_and_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _flush_structlog_proxy_caches()
    try:
        with structlog.testing.capture_logs() as logs:
            binding = _authority(tmp_path).acquire_launch_binding(
                backend=ClaudeCodeBackend(),
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            identity = binding.identity
            binding.close()
    finally:
        _flush_structlog_proxy_caches()

    lifecycle = [entry for entry in logs if entry.get("event") == "plugin_artifact_lifecycle"]
    assert [entry["action"] for entry in lifecycle] == [
        "publish",
        "acquire",
        "release",
    ]
    assert all(entry["outcome"] == "succeeded" for entry in lifecycle)
    assert all(entry["artifact_kind"] == "projection" for entry in lifecycle)
    assert all(entry["semantic_key"] == identity.semantic_key for entry in lifecycle)
    assert all(entry["incarnation"] == identity.incarnation_id for entry in lifecycle)


def test_projection_reclaim_io_failure_stays_queued_for_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import autoskillit.core._plugin_cache as plugin_cache
    from autoskillit.workspace import ProjectedPluginRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binding = _authority(tmp_path).acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    identity = binding.identity
    binding.close()
    owner = ProjectedPluginRetirementOwner(
        identity.managed_path.parent,
        home=managed_home(),
    )
    deadline = datetime.now(UTC)
    append_result = owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]

    real_rmtree = plugin_cache.shutil.rmtree

    def fail_reclaim(_path):
        raise PermissionError("injected projection reclaim failure")

    monkeypatch.setattr(plugin_cache.shutil, "rmtree", fail_reclaim)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_IO_ERROR
    assert append_result.record_id in {
        queued.record_id for queued in read_retiring_cache().records
    }
    monkeypatch.setattr(plugin_cache.shutil, "rmtree", real_rmtree)
    assert owner.try_reclaim(record, deadline) is RetirementOutcome.RECLAIMED
    assert append_result.record_id not in {
        queued.record_id for queued in read_retiring_cache().records
    }


def test_projection_reclaim_preserves_outcome_when_writer_close_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.workspace._projection_cache import projected_artifact_lease_path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binding = _authority(tmp_path).acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    identity = binding.identity
    binding.close()

    from autoskillit.workspace import ProjectedPluginRetirementOwner

    owner = ProjectedPluginRetirementOwner(
        identity.managed_path.parent,
        home=managed_home(),
    )
    deadline = datetime.now(UTC)
    owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]
    real_close = ArtifactLease.close
    close_calls = 0

    def fail_after_close(lease: ArtifactLease) -> None:
        nonlocal close_calls
        close_calls += 1
        real_close(lease)
        raise OSError("injected retirement writer close failure")

    monkeypatch.setattr(ArtifactLease, "close", fail_after_close)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.RECLAIMED
    assert close_calls == 1
    monkeypatch.setattr(ArtifactLease, "close", real_close)
    with ArtifactLease.acquire_exclusive(
        projected_artifact_lease_path(identity.managed_path),
        blocking=False,
    ):
        pass


def test_projection_prune_allows_terminal_reconciliation_when_writer_close_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.workspace._projection_cache import projected_artifact_lease_path

    projections_root = tmp_path / "projections"
    stale = projections_root / ("a" * 24)
    stale.mkdir(parents=True)
    real_close = ArtifactLease.close
    close_calls = 0

    def fail_after_close(lease: ArtifactLease) -> None:
        nonlocal close_calls
        close_calls += 1
        real_close(lease)
        raise OSError("injected prune writer close failure")

    monkeypatch.setattr(ArtifactLease, "close", fail_after_close)

    assert (
        prune_stale_projections(
            projections_root,
            home=managed_home_for(tmp_path),
            active_key="active",
        )
        == 0
    )
    assert close_calls == 1
    assert not stale.exists()
    monkeypatch.setattr(ArtifactLease, "close", real_close)
    with ArtifactLease.acquire_exclusive(
        projected_artifact_lease_path(stale),
        blocking=False,
    ):
        pass


def test_corrupt_live_incarnation_is_not_replaced_until_reader_closes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()
    binding = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    old_identity = binding.identity
    probe = old_identity.managed_path / "recipes" / "_lifetime_probe.yaml"
    probe.write_text("corrupt: true\n", encoding="utf-8")
    manifest_bytes = old_identity.manifest_path.read_bytes()

    try:
        with pytest.raises(PluginArtifactContentionError):
            authority.acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
        assert probe.is_file()
        assert old_identity.manifest_path.read_bytes() == manifest_bytes
    finally:
        binding.close()

    with authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as replacement:
        assert replacement.identity.managed_path == old_identity.managed_path
        assert replacement.identity.incarnation_id != old_identity.incarnation_id
        assert not probe.exists()


def test_transient_projection_io_does_not_trigger_destructive_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import autoskillit.workspace._projection_cache as projection_cache

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()
    with authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as initial:
        identity = initial.identity
    manifest_before = identity.manifest_path.read_bytes()
    original_digest = projection_cache.directory_tree_digest

    def fail_digest(_path: Path) -> str:
        raise PermissionError("injected transient projection digest failure")

    monkeypatch.setattr(projection_cache, "directory_tree_digest", fail_digest)

    with pytest.raises(PluginArtifactUnavailableError, match="cannot be read for digest"):
        authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )

    assert identity.managed_path.is_dir()
    assert identity.manifest_path.read_bytes() == manifest_before
    monkeypatch.setattr(projection_cache, "directory_tree_digest", original_digest)
    with authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as revalidated:
        assert revalidated.identity.incarnation_id == identity.incarnation_id


def test_mode_only_mutation_invalidates_projection_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    backend = ClaudeCodeBackend()
    binding = authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    plugin_metadata = binding.identity.managed_path / ".claude-plugin" / "plugin.json"
    plugin_metadata.chmod(plugin_metadata.stat().st_mode ^ 0o100)

    try:
        with pytest.raises(PluginArtifactContentionError):
            authority.acquire_launch_binding(
                backend=backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
    finally:
        binding.close()

    with authority.acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as replacement:
        assert replacement.identity.incarnation_id != binding.identity.incarnation_id


def test_writer_to_reader_handoff_revalidates_exact_incarnation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)
    original_acquire = ArtifactLease.acquire_shared
    acquisitions = 0

    def acquire_shared(cls: type[ArtifactLease], lock_path: Path) -> ArtifactLease:
        nonlocal acquisitions
        del cls
        lease = original_acquire(lock_path)
        acquisitions += 1
        if acquisitions == 2:
            projections = lock_path.parent.parent
            semantic_key = lock_path.stem
            manifest_path = projections / f".{semantic_key}.autoskillit-projection.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["incarnation_id"] = new_plugin_artifact_incarnation_id()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return lease

    monkeypatch.setattr(ArtifactLease, "acquire_shared", classmethod(acquire_shared))

    with pytest.raises(PluginArtifactValidationError, match="incarnation changed"):
        authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )

    plan = authority._plan(ClaudeCodeBackend())
    with ArtifactLease.acquire_exclusive(plan.lease_path, blocking=False):
        pass


@pytest.mark.parametrize(
    "load_mode",
    [
        PluginLoadMode.GENERATED_HOME,
        PluginLoadMode.NONE,
    ],
)
def test_projected_authority_rejects_incompatible_load_modes(
    tmp_path: Path,
    monkeypatch,
    load_mode: PluginLoadMode,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    authority = _authority(tmp_path)

    with pytest.raises(ValueError):
        authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=load_mode,
        )
    assert not (tmp_path / ".autoskillit").exists()
