"""The generation store must actually be reclaimable, and never eat its own infrastructure.

Three defects motivated these tests, all shipped together and all invisible to the
prior suite:

1. ``default_plugin_retirement_coordinator()`` routed every ``INSTALLED_PLUGIN``
   record to an owner rooted at the *legacy* Claude plugin cache, while
   ``publish_generation()`` writes records under ``~/.autoskillit/plugin-generations``.
   ``try_reclaim`` rejects an uncontained record **without removing it**, so no
   generation was ever reclaimable — the queue only grew.
2. Retirement was enqueued per-version, so a superseded *version* was never
   queued at all.
3. Migrated ``legacy_evidence`` recorded ``plugin-projections/.artifact-leases`` —
   the directory holding every live session's lease locks — as a ``projection``.
   Anything that reclaims legacy evidence by trusting that stored classification
   deletes the lease infrastructure out from under running sessions.

Every test here fails against the pre-fix code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoskillit.core import (
    LegacyRetiringEvidence,
    PluginArtifactIdentity,
    PluginArtifactKind,
    RetirementOutcome,
    _InstallLock,
    generation_plugin_selector_path,
    is_reclaimable_artifact_path,
    read_retiring_cache,
    resolve_current_generation_for_plugin,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PLUGIN_REF = "autoskillit"


def _publish(home: Path, source_root: Path, version: str) -> PluginArtifactIdentity:
    from autoskillit.workspace import publish_generation

    with _InstallLock():
        return publish_generation(
            home=home,
            plugin_ref=_PLUGIN_REF,
            version=version,
            semantic_key=f"autoskillit@autoskillit-local:{version}",
            source_root=source_root,
        )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "_dispatch.py").write_text("# dispatcher\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# The infrastructure landmine
# ---------------------------------------------------------------------------


def test_lease_directory_is_never_a_reclaimable_artifact() -> None:
    """``.artifact-leases`` holds every live session's lock; it is not an artifact."""
    managed_root = Path("/managed/plugin-projections")

    assert not is_reclaimable_artifact_path(managed_root / ".artifact-leases", managed_root)
    assert not is_reclaimable_artifact_path(managed_root / ".anything-hidden", managed_root)
    assert is_reclaimable_artifact_path(managed_root / "0a1064ba3c624c945ca0e2df", managed_root)


def test_nested_paths_are_not_artifacts() -> None:
    """An artifact is exactly one level deep; deeper paths are its components."""
    managed_root = Path("/managed/plugin-projections")

    assert not is_reclaimable_artifact_path(managed_root / "abc" / "hooks", managed_root)
    assert not is_reclaimable_artifact_path(managed_root, managed_root)


def test_classification_refuses_infrastructure_paths(tmp_path: Path) -> None:
    """Migration must not record the lease directory as a retirable projection."""
    from autoskillit.core._plugin_cache import _classify_legacy_path

    projections = tmp_path / "plugin-projections"
    leases = projections / ".artifact-leases"
    leases.mkdir(parents=True)
    roots = {PluginArtifactKind.PROJECTION: projections}

    kind, reason = _classify_legacy_path(str(leases), roots)

    assert kind is None
    assert reason is not None and "infrastructure" in reason


def test_promotion_refuses_infrastructure_even_when_evidence_says_projection(
    tmp_path: Path,
) -> None:
    """Already-persisted evidence carries the bad classification; re-derive it.

    The stored ``recognized_kind`` is exactly what the old migration got wrong,
    so the sweep must never treat it as authority.
    """
    from autoskillit.workspace import ProjectedPluginRetirementOwner

    projections = tmp_path / "plugin-projections"
    leases = projections / ".artifact-leases"
    leases.mkdir(parents=True)
    (leases / "somehash.lock").write_text("", encoding="utf-8")

    owner = ProjectedPluginRetirementOwner(projections)
    evidence = LegacyRetiringEvidence(
        record_id="deadbeef",
        version="projection:.artifact-leases",
        path=str(leases),
        retired_at="2026-07-29T03:18:17.568199+00:00",
        recognized_kind=PluginArtifactKind.PROJECTION,
        rejection_reason=None,
    )

    outcome = owner.try_promote_legacy_evidence(evidence, datetime.now(UTC))

    assert outcome is RetirementOutcome.LEGACY_EVIDENCE
    assert leases.is_dir(), "the live lease directory must survive"
    assert (leases / "somehash.lock").is_file()


def test_promotion_drops_bookkeeping_for_already_gone_paths(tmp_path: Path) -> None:
    """A duplicate row for an already-deleted artifact resolves with no I/O."""
    from autoskillit.core import append_retiring_record  # noqa: F401  (cache bootstrap)
    from autoskillit.workspace import ProjectedPluginRetirementOwner

    projections = tmp_path / "plugin-projections"
    projections.mkdir(parents=True)
    owner = ProjectedPluginRetirementOwner(projections)
    evidence = LegacyRetiringEvidence(
        record_id="cafebabe",
        version="projection:gone",
        path=str(projections / "gone"),
        retired_at="2026-07-29T03:18:17.568199+00:00",
        recognized_kind=PluginArtifactKind.PROJECTION,
        rejection_reason=None,
    )

    outcome = owner.try_promote_legacy_evidence(evidence, datetime.now(UTC))

    assert outcome is RetirementOutcome.RECORD_REMOVED


# ---------------------------------------------------------------------------
# Cross-version retirement + correct routing
# ---------------------------------------------------------------------------


def test_superseded_version_is_enqueued_on_publish(home: Path, source_root: Path) -> None:
    """A new version must queue the prior version, not just prior incarnations."""
    first = _publish(home, source_root, "1.0.0")
    (source_root / "hooks" / "_dispatch.py").write_text("# v2\n", encoding="utf-8")
    second = _publish(home, source_root, "2.0.0")

    queued = {record.managed_path for record in read_retiring_cache().records}

    assert first.managed_path in queued, "the superseded 1.0.0 generation must be queued"
    assert second.managed_path not in queued, (
        "the currently selected generation must not be queued"
    )


def test_queued_generation_is_reclaimable_by_the_default_coordinator(
    home: Path,
    source_root: Path,
) -> None:
    """The regression: records were routed to an owner that could never contain them."""
    from autoskillit.cli._plugin_artifact import default_plugin_retirement_coordinator

    first = _publish(home, source_root, "1.0.0")
    (source_root / "hooks" / "_dispatch.py").write_text("# v2\n", encoding="utf-8")
    _publish(home, source_root, "2.0.0")

    coordinator = default_plugin_retirement_coordinator()
    outcomes = coordinator.sweep_due(datetime.now(UTC) + timedelta(days=2))

    assert RetirementOutcome.RECLAIMED in outcomes
    assert not first.managed_path.exists(), "the superseded generation must be removed"


def test_selected_generations_are_never_reclaimed(home: Path, source_root: Path) -> None:
    """Both selectors protect: per-version current and the plugin-level current."""
    from autoskillit.cli._plugin_artifact import default_plugin_retirement_coordinator

    _publish(home, source_root, "1.0.0")
    (source_root / "hooks" / "_dispatch.py").write_text("# v2\n", encoding="utf-8")
    current = _publish(home, source_root, "2.0.0")

    coordinator = default_plugin_retirement_coordinator()
    outcomes = coordinator.sweep_due(datetime.now(UTC) + timedelta(days=2))

    assert RetirementOutcome.RECLAIMED in outcomes, (
        "the superseded 1.0.0 generation must be reclaimed"
    )
    assert current.managed_path.is_dir()
    assert resolve_current_generation_for_plugin(home, _PLUGIN_REF) == current.managed_path


def test_every_artifact_kind_has_a_registered_owner(home: Path) -> None:
    """A new storage location without an owner silently repeats the routing bug."""
    from autoskillit.cli._plugin_artifact import default_plugin_retirement_coordinator

    coordinator = default_plugin_retirement_coordinator()

    assert frozenset(coordinator._owners) == frozenset(PluginArtifactKind)


# ---------------------------------------------------------------------------
# The version-independent selector Codex pins
# ---------------------------------------------------------------------------


def test_plugin_selector_survives_a_version_bump(home: Path, source_root: Path) -> None:
    """Codex bakes this absolute path into config; it must not dangle on bump."""
    _publish(home, source_root, "1.0.0")
    selector = generation_plugin_selector_path(home, _PLUGIN_REF)
    dispatcher = selector / "hooks" / "_dispatch.py"

    assert dispatcher.is_file()

    (source_root / "hooks" / "_dispatch.py").write_text("# v2\n", encoding="utf-8")
    second = _publish(home, source_root, "2.0.0")

    assert dispatcher.is_file(), "the pinned path must still resolve after a bump"
    assert selector.resolve() == second.managed_path


def test_codex_hooks_resolve_through_the_plugin_selector(
    home: Path,
    source_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bindingless resolver must prefer the version-independent path."""
    from autoskillit.execution.backends._codex_hooks import _resolve_codex_hooks_dir

    _publish(home, source_root, "1.0.0")
    monkeypatch.setattr("autoskillit.__version__", "9.9.9", raising=False)

    resolved = _resolve_codex_hooks_dir()

    assert resolved == generation_plugin_selector_path(home, _PLUGIN_REF) / "hooks"
    assert (resolved / "_dispatch.py").is_file()
