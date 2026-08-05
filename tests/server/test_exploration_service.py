"""Integration coverage for the server-owned deterministic collector gateway."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    CollectorStatus,
    ExplorationApplicability,
    ExplorationQuerySpec,
    ProfileActivation,
    RepositoryProfileId,
)
from autoskillit.exploration._deterministic import CursorValidationError
from autoskillit.exploration.collectors import COLLECTOR_PROFILES, CollectorInvocation
from autoskillit.exploration.snapshot import SnapshotCaptureResult, SnapshotCaptureStatus
from autoskillit.pipeline import CapabilityResolutionStatus, OwnerBoundExplorationContextStore
from autoskillit.server import _exploration_service
from autoskillit.server._exploration_service import DefaultExplorationService

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _seed_repository(root: Path) -> None:
    root.mkdir()
    (root / "module.py").write_text("def needle() -> str:\n    return 'needle'\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "module.py"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AutoSkillit Test",
            "-c",
            "user.email=autoskillit@example.invalid",
            "commit",
            "-qm",
            "seed",
        ],
        cwd=root,
        check=True,
    )


def _seed_non_python_repository(root: Path) -> None:
    root.mkdir()
    (root / "README.md").write_text("needle appears only in this language-neutral artifact\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AutoSkillit Test",
            "-c",
            "user.email=autoskillit@example.invalid",
            "commit",
            "-qm",
            "seed",
        ],
        cwd=root,
        check=True,
    )


def test_default_service_executes_profile_scoped_collectors_and_rejects_replay_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _seed_repository(root)
    service = DefaultExplorationService()
    issuance_snapshot = service.capture_snapshot(root)
    context = service.collect(ExplorationQuerySpec("needle", scope=("module.py",)), root=root)

    assert issuance_snapshot.digest == context.snapshot.digest
    assert context.completeness.complete
    reports = {report.collector_id: report for report in context.completeness.reports}
    assert set(reports) == {
        collector.collector_id
        for collector in COLLECTOR_PROFILES
        if collector.profile
        in {RepositoryProfileId.LANGUAGE_NEUTRAL, RepositoryProfileId.GENERIC_PYTHON}
    }
    assert reports["native-lsp"].status is CollectorStatus.UNSUPPORTED
    assert reports["native-tree-sitter"].status is CollectorStatus.UNSUPPORTED
    assert context.evidence
    assert all(record.method and record.extractor_version for record in context.evidence)
    assert all(record.searched_scope and record.location for record in context.evidence)
    assert all(record.query_uncertainty for record in context.evidence)

    first = service.page(context, page_size=1)
    assert first.graph_nodes
    graph_page = service.page(context, page_size=100)
    assert graph_page.graph_edges
    assert first.continuation is not None
    with pytest.raises(CursorValidationError, match="stale"):
        service.page(
            replace(context, query=ExplorationQuerySpec("different query")),
            page_size=1,
            cursor=first.continuation,
        )


def test_default_service_routes_discovered_cross_profile_frontiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.exploration import reclassify_cross_leaf as real_reclassify

    root = tmp_path / "repository"
    _seed_repository(root)
    observed_handoffs: list[dict[str, RepositoryProfileId]] = []

    def record_reclassification(items, *, handoffs):
        observed_handoffs.append(dict(handoffs))
        return real_reclassify(items, handoffs=handoffs)

    monkeypatch.setattr(
        _exploration_service,
        "reclassify_cross_leaf",
        record_reclassification,
    )

    DefaultExplorationService().collect(
        ExplorationQuerySpec("needle", scope=("module.py",)),
        root=root,
    )

    assert observed_handoffs
    assert RepositoryProfileId.GENERIC_PYTHON in observed_handoffs[0].values()


def test_shared_principal_rejects_repository_mutation_after_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    _seed_repository(root)
    execution_cwd = tmp_path / "sterile-agent-cwd"
    execution_cwd.mkdir()
    authority_home = tmp_path / "session"
    authority_home.mkdir()
    service = DefaultExplorationService()
    parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=root,
        service=service,
    )
    bindings = parent.bind_launches(
        owner_id="uid:1000",
        session_id="session-a",
        cwd=execution_cwd,
        repository_root=root,
        source_identities={
            "semantic-code-navigator": "navigator-definition-a:parent-source",
            "repository-impact-profiler": "profiler-definition-a:parent-source",
        },
        authority_home=authority_home,
    )
    for key, value in bindings["semantic-code-navigator"].items():
        monkeypatch.setenv(key, value)
    assert list(execution_cwd.iterdir()) == []
    (root / "module.py").write_text("def changed() -> str:\n    return 'changed'\n")
    child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=root,
        service=DefaultExplorationService(),
    )
    monkeypatch.chdir(execution_cwd)

    status, page = child.submit_from_launch_environment(
        query=ExplorationQuerySpec("changed", scope=("module.py",)),
        page_size=10,
    )

    assert status is CapabilityResolutionStatus.INVALID
    assert page is None


def test_shared_principal_reopens_cross_process_and_cleanup_revokes_cached_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    _seed_repository(root)
    execution_cwd = tmp_path / "sterile-agent-cwd"
    execution_cwd.mkdir()
    authority_home = tmp_path / "session"
    authority_home.mkdir()
    parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=root,
        service=DefaultExplorationService(),
    )
    bindings = parent.bind_launches(
        owner_id="uid:1000",
        session_id="session-a",
        cwd=execution_cwd,
        repository_root=root,
        source_identities={
            "semantic-code-navigator": "navigator-definition-a:parent-source",
            "repository-impact-profiler": "profiler-definition-a:parent-source",
        },
        authority_home=authority_home,
    )
    for key, value in bindings["repository-impact-profiler"].items():
        monkeypatch.setenv(key, value)
    child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=root,
        service=DefaultExplorationService(),
    )
    monkeypatch.chdir(execution_cwd)

    status, page = child.submit_from_launch_environment(
        query=ExplorationQuerySpec("needle", scope=("module.py",)),
        page_size=10,
    )
    assert status is CapabilityResolutionStatus.OK
    assert page is not None

    parent.cleanup_session("session-a")

    assert child.validate_launch_environment() is False
    status, page = child.get_page_from_launch_environment(page_size=10)
    assert status is CapabilityResolutionStatus.INVALID
    assert page is None


def test_planning_activates_optional_and_specialized_collectors_for_trusted_profiles() -> None:
    query = ExplorationQuerySpec("needle", required_profiles=(RepositoryProfileId.GENERIC_PYTHON,))
    collectors = DefaultExplorationService._planned_collectors(
        query,
        (
            ProfileActivation(
                RepositoryProfileId.LANGUAGE_NEUTRAL, ExplorationApplicability.APPLICABLE, "base"
            ),
            ProfileActivation(
                RepositoryProfileId.GENERIC_PYTHON, ExplorationApplicability.APPLICABLE, "python"
            ),
            ProfileActivation(
                RepositoryProfileId.AUTOSKILLIT, ExplorationApplicability.APPLICABLE, "trusted"
            ),
        ),
    )

    assert {collector.collector_id for collector in collectors} == {
        collector.collector_id for collector in COLLECTOR_PROFILES
    }
    assert {
        collector.collector_id for collector in collectors if collector.required_by_default
    } == {
        "bounded-rg-search",
        "contained-list",
        "python-ast",
        "autoskillit-registry",
    }


def test_inapplicable_profiles_are_visible_without_claiming_optional_completeness(
    tmp_path: Path,
) -> None:
    root = tmp_path / "language-neutral-repository"
    _seed_non_python_repository(root)
    query = ExplorationQuerySpec(
        "  needle  ",
        required_profiles=(RepositoryProfileId.GENERIC_PYTHON, RepositoryProfileId.AUTOSKILLIT),
    )
    service = DefaultExplorationService()

    context = service.collect(query, root=root)
    reports = {report.collector_id: report for report in context.completeness.reports}

    assert reports["bounded-rg-search"].status is CollectorStatus.SUCCEEDED
    assert reports["python-ast"].status is CollectorStatus.UNSUPPORTED
    assert reports["autoskillit-registry"].status is CollectorStatus.UNSUPPORTED
    assert "not applicable" in (reports["python-ast"].diagnostic or "")
    assert "not applicable" in (reports["autoskillit-registry"].diagnostic or "")
    assert not context.completeness.complete
    assert context.completeness.failed_collectors == (
        "autoskillit-registry",
        "python-ast",
    )


def test_service_rejects_mutation_that_occurs_during_real_collector_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _seed_repository(root)
    search_profile = next(
        profile for profile in COLLECTOR_PROFILES if profile.collector_id == "bounded-rg-search"
    )

    def mutate_after_search(root_arg, snapshot_digest, task_query, scopes, limits):
        reports = search_profile.invocation(root_arg, snapshot_digest, task_query, scopes, limits)
        (root / "module.py").write_text("def changed() -> str:\n    return 'changed'\n")
        return reports

    mutating_invocation = CollectorInvocation(
        search_profile.collector_id,
        "test-mutate-after-search",
        mutate_after_search,
    )

    monkeypatch.setattr(
        _exploration_service,
        "COLLECTOR_PROFILES",
        tuple(
            replace(profile, invocation=mutating_invocation)
            if profile.collector_id == search_profile.collector_id
            else profile
            for profile in COLLECTOR_PROFILES
        ),
    )

    with pytest.raises(RuntimeError, match="publication rejected"):
        DefaultExplorationService().collect(ExplorationQuerySpec("needle"), root=root)


def test_service_routes_with_capture_validated_activation_and_recaptures_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "language-neutral-repository"
    _seed_non_python_repository(root)
    captured = _exploration_service.capture_repository_snapshot(
        root,
        collector_manifest_digest=_exploration_service.collector_manifest_digest(),
    )
    assert captured.validated_activation is not None
    (root / "appeared-after-capture.py").write_text("needle = 1\n")
    captures = 0

    def return_validated_capture(*args: object, **kwargs: object) -> SnapshotCaptureResult:
        nonlocal captures
        captures += 1
        return captured

    monkeypatch.setattr(
        _exploration_service, "capture_repository_snapshot", return_validated_capture
    )

    context = DefaultExplorationService().collect(ExplorationQuerySpec("needle"), root=root)

    assert captures == 2
    assert {report.collector_id for report in context.completeness.reports} == {
        profile.collector_id
        for profile in COLLECTOR_PROFILES
        if profile.profile is RepositoryProfileId.LANGUAGE_NEUTRAL
    }


def test_service_rejects_a_truncated_post_collection_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _seed_repository(root)
    real_capture = _exploration_service.capture_repository_snapshot
    captures = 0

    def truncate_second_capture(*args: object, **kwargs: object) -> SnapshotCaptureResult:
        nonlocal captures
        captures += 1
        result = real_capture(*args, **kwargs)
        if captures != 2:
            return result
        assert result.snapshot is not None
        terminal = replace(
            result.snapshot,
            state="truncated",
            truncated=True,
            truncation_reason="test limit",
        )
        return SnapshotCaptureResult(SnapshotCaptureStatus.TRUNCATED, terminal, "test limit")

    monkeypatch.setattr(
        _exploration_service, "capture_repository_snapshot", truncate_second_capture
    )

    with pytest.raises(RuntimeError, match="publication rejected"):
        DefaultExplorationService().collect(ExplorationQuerySpec("needle"), root=root)
