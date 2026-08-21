"""Managed-home binding contracts for plugin retirement."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.cli.install._plugin_artifact import (
    InstalledPluginArtifactRetirementOwner,
)
from autoskillit.core import (
    ManagedHome,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactRetirementEngine,
    PluginArtifactValidationError,
    RetirementOutcome,
    managed_home_for,
    read_retiring_cache,
)
from autoskillit.workspace._projected_artifact._generation_publication import (
    GenerationArtifactRetirementOwner,
)
from autoskillit.workspace._projection_cache import ProjectedPluginRetirementOwner

pytestmark = pytest.mark.medium


def _identity(managed_root: Path) -> PluginArtifactIdentity:
    managed_path = managed_root / "generation"
    return PluginArtifactIdentity(
        semantic_key="plugin:generation",
        incarnation_id="00000000000040008000000000000001",
        manifest_schema_version=1,
        artifact_digest="a" * 64,
        managed_path=managed_path,
        manifest_path=managed_root / ".generation.manifest.json",
    )


def _engine(home: ManagedHome, managed_root: Path) -> PluginArtifactRetirementEngine:
    identity = _identity(managed_root)
    return PluginArtifactRetirementEngine(
        home=home,
        managed_root=managed_root,
        artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
        manifest_path=lambda _path: identity.manifest_path,
        lease_path=lambda path: path.parent / f".{path.name}.lock",
        current_identity=lambda _record: identity,
        logger=Mock(),
        is_current=None,
    )


def test_managed_home_is_factory_only_and_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be built"):
        ManagedHome(tmp_path)
    with pytest.raises(ValueError, match="must be absolute"):
        managed_home_for(Path("relative-home"))


def test_managed_home_exposes_one_containment_root(tmp_path: Path) -> None:
    home = managed_home_for(tmp_path)

    assert home.autoskillit_dir == tmp_path / ".autoskillit"
    assert home.contains(tmp_path / ".autoskillit" / "state.json") is True
    assert home.contains(tmp_path.parent / "outside") is False
    assert Path(home) == tmp_path


def test_enqueue_refuses_a_managed_root_outside_the_writing_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    validating_root = tmp_path / "validating-home"
    writing_root = tmp_path / "writing-home"
    validating_root.mkdir()
    writing_root.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: writing_root)
    managed_root = validating_root / ".autoskillit" / "artifacts"
    engine = _engine(managed_home_for(writing_root), managed_root)

    with pytest.raises(PluginArtifactValidationError) as exc_info:
        engine.enqueue_retirement(_identity(managed_root), datetime.now(UTC) + timedelta(hours=1))

    message = str(exc_info.value)
    assert str(validating_root) in message
    assert str(writing_root) in message
    assert not (validating_root / ".autoskillit").exists()
    assert not (writing_root / ".autoskillit").exists()


def test_engine_writes_only_under_the_home_it_was_bound_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bound_root = tmp_path / "bound-home"
    ambient_root = tmp_path / "ambient-home"
    bound_root.mkdir()
    ambient_root.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: ambient_root)
    managed_root = bound_root / ".autoskillit" / "artifacts"
    engine = _engine(managed_home_for(bound_root), managed_root)

    result = engine.enqueue_retirement(
        _identity(managed_root), datetime.now(UTC) + timedelta(hours=1)
    )

    assert result is not None
    cache = bound_root / ".autoskillit" / "retiring_cache.json"
    assert json.loads(cache.read_text(encoding="utf-8"))["records"][0]["managed_path"] == str(
        managed_root / "generation"
    )
    assert not (ambient_root / ".autoskillit").exists()


def test_engine_cancels_only_under_the_home_it_was_bound_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bound_root = tmp_path / "bound-home"
    ambient_root = tmp_path / "ambient-home"
    bound_root.mkdir()
    ambient_root.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: ambient_root)
    home = managed_home_for(bound_root)
    managed_root = bound_root / ".autoskillit" / "artifacts"
    identity = _identity(managed_root)
    engine = _engine(home, managed_root)
    appended = engine.enqueue_retirement(identity, datetime.now(UTC) + timedelta(hours=1))
    assert appended is not None

    cancelled = engine.cancel_obsolete_retirements(identity)

    assert cancelled == (appended.record_id,)
    assert read_retiring_cache(home=home).records == ()
    assert not (ambient_root / ".autoskillit").exists()


def test_engine_reclaims_only_under_the_home_it_was_bound_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bound_root = tmp_path / "bound-home"
    ambient_root = tmp_path / "ambient-home"
    bound_root.mkdir()
    ambient_root.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: ambient_root)
    home = managed_home_for(bound_root)
    managed_root = bound_root / ".autoskillit" / "artifacts"
    managed_root.mkdir(parents=True)
    engine = _engine(home, managed_root)
    now = datetime.now(UTC)
    appended = engine.enqueue_retirement(_identity(managed_root), now)
    assert appended is not None
    record = read_retiring_cache(home=home).records[0]

    outcome = engine.try_reclaim(record, now + timedelta(seconds=1))

    assert outcome is RetirementOutcome.RECORD_REMOVED
    assert read_retiring_cache(home=home).records == ()
    assert not (ambient_root / ".autoskillit").exists()


def test_every_retirement_owner_binds_to_a_managed_home(tmp_path: Path) -> None:
    home = managed_home_for(tmp_path)
    owners = (
        ProjectedPluginRetirementOwner(tmp_path / "projections", home=home),
        InstalledPluginArtifactRetirementOwner(tmp_path / "installed", home=home),
        GenerationArtifactRetirementOwner(
            tmp_path / "generations", home=home, plugin_ref="plugin"
        ),
    )

    for owner in owners:
        assert owner._retirement._home is home
