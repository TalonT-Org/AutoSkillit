"""A session home pins the projection it bakes until the home is gone."""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoskillit.core import (
    SESSION_STALE_SECONDS,
    PluginArtifactIdentity,
    PluginLoadMode,
    RetirementOutcome,
    SessionHookRoot,
    managed_home,
    read_retiring_cache,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import (
    ProjectedPluginRetirementOwner,
    SkillsDirectoryProvider,
    live_projected_artifact_referrers,
    projected_artifact_referrer_dir,
    prune_stale_projections,
    record_projected_artifact_referrer,
)
from autoskillit.workspace.session_skills import DefaultSessionSkillManager
from tests.contracts._projection_helpers import projected_plugin_authority
from tests.workspace._helpers import _materialize

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_OTHER_ACTIVE_KEY = "f" * 24


def _closed_projection_identity(tmp_path: Path) -> PluginArtifactIdentity:
    """Bind a projection and release the binding, leaving no reader lease held."""
    binding = projected_plugin_authority(tmp_path).acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    identity = binding.identity
    binding.close()
    return identity


def _codex_profile_source(tmp_path: Path) -> Path:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
    (source_home / "config.toml").write_text(
        'cli_auth_credentials_store = "keyring"\n', encoding="utf-8"
    )
    return source_home


def _session_skill_manager(tmp_path: Path) -> DefaultSessionSkillManager:
    # cleanup_stale sweeps every direct child of ephemeral_root and every persistent
    # root, so neither may be tmp_path itself: that would also sweep the monkeypatched
    # home's .autoskillit state (the projection this test is pinning).
    ephemeral_root = tmp_path / "ephemeral"
    ephemeral_root.mkdir(exist_ok=True)
    return DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=ephemeral_root,
        persistent_roots={"codex": tmp_path / "codex-root"},
    )


def test_referrer_pin_defers_reclaim_while_the_home_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _closed_projection_identity(tmp_path)
    session_home = tmp_path / "session-home"
    session_home.mkdir()
    record_projected_artifact_referrer(identity.managed_path, session_home)

    owner = ProjectedPluginRetirementOwner(identity.managed_path.parent, home=managed_home())
    deadline = datetime.now(UTC)
    append_result = owner.enqueue_retirement(identity, deadline)
    assert append_result is not None
    record = next(
        item for item in read_retiring_cache().records if item.record_id == append_result.record_id
    )
    referrer_dir = projected_artifact_referrer_dir(identity.managed_path)
    (referrer_file,) = list(referrer_dir.iterdir())

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_CONTENDED
    assert identity.managed_path.is_dir()
    assert referrer_file.is_file()

    shutil.rmtree(session_home)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.RECLAIMED
    assert not identity.managed_path.exists()
    assert not referrer_file.exists()


def test_crashed_launcher_home_pins_projection_until_cleanup_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    backend = CodexBackend(source_codex_home=_codex_profile_source(tmp_path))
    binding = projected_plugin_authority(tmp_path).acquire_launch_binding(
        backend=backend,
        load_mode=PluginLoadMode.PROJECTED_HOME,
    )
    root = SessionHookRoot.from_binding(binding)

    manager = _session_skill_manager(tmp_path)
    add_dir = _materialize(
        manager,
        "crashed-launcher",
        backend=backend,
        names=frozenset({"make-arch-diag"}),
        session_hook_root=root,
    )
    generated_home = Path(add_dir.session_home)
    # A crashed process drops every OS-level lock it held, including the session's
    # own generated-home lease; only the referrer pin below should keep the
    # projection alive after that.
    manager._session_leases["crashed-launcher"].release()

    binding.close()

    owner = ProjectedPluginRetirementOwner(
        root.artifact_path.parent, home=managed_home(), active_key=_OTHER_ACTIVE_KEY
    )
    prune_stale_projections(
        root.artifact_path.parent,
        home=managed_home(),
        active_key=_OTHER_ACTIVE_KEY,
    )
    record = next(
        item for item in read_retiring_cache().records if item.managed_path == root.artifact_path
    )

    assert owner.try_reclaim(record, record.not_before) is RetirementOutcome.DEFERRED_CONTENDED
    assert root.artifact_path.is_dir()

    successor = _session_skill_manager(tmp_path)
    removed = successor.cleanup_stale(max_age_seconds=0)
    assert removed >= 1
    assert not generated_home.exists()

    assert owner.try_reclaim(record, record.not_before) is RetirementOutcome.RECLAIMED
    assert not root.artifact_path.exists()


def test_evaluation_prunes_a_referrer_whose_home_is_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _closed_projection_identity(tmp_path)
    referrer_dir = projected_artifact_referrer_dir(identity.managed_path)

    vanished_home = tmp_path / "vanished-home"
    vanished_home.mkdir()
    record_projected_artifact_referrer(identity.managed_path, vanished_home)
    (stale_referrer,) = list(referrer_dir.iterdir())
    shutil.rmtree(vanished_home)

    assert live_projected_artifact_referrers(identity.managed_path) == ()
    assert not stale_referrer.exists()


def test_recording_a_new_referrer_prunes_stale_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _closed_projection_identity(tmp_path)
    referrer_dir = projected_artifact_referrer_dir(identity.managed_path)

    vanished_home = tmp_path / "vanished-home"
    vanished_home.mkdir()
    record_projected_artifact_referrer(identity.managed_path, vanished_home)
    (stale_referrer,) = list(referrer_dir.iterdir())
    shutil.rmtree(vanished_home)

    live_home = tmp_path / "live-home"
    live_home.mkdir()
    record_projected_artifact_referrer(identity.managed_path, live_home)

    assert not stale_referrer.exists()
    assert live_projected_artifact_referrers(identity.managed_path) == (
        Path(os.path.realpath(live_home)),
    )


def test_referrer_pruned_once_its_home_exceeds_the_staleness_horizon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _closed_projection_identity(tmp_path)
    referrer_dir = projected_artifact_referrer_dir(identity.managed_path)

    aged_home = tmp_path / "aged-home"
    aged_home.mkdir()
    record_projected_artifact_referrer(identity.managed_path, aged_home)
    (stale_referrer,) = list(referrer_dir.iterdir())
    old = time.time() - SESSION_STALE_SECONDS - 1
    os.utime(aged_home, (old, old))

    assert live_projected_artifact_referrers(identity.managed_path) == ()
    assert not stale_referrer.exists()


def test_unreadable_referrer_is_retained_while_fresh_and_pruned_once_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _closed_projection_identity(tmp_path)
    referrer_dir = projected_artifact_referrer_dir(identity.managed_path)
    referrer_dir.mkdir(parents=True, exist_ok=True)
    malformed = referrer_dir / f"{'0' * 24}.json"
    malformed.write_text("{not-json", encoding="utf-8")

    live_projected_artifact_referrers(identity.managed_path)
    assert malformed.is_file()

    old = time.time() - SESSION_STALE_SECONDS - 1
    os.utime(malformed, (old, old))

    live_projected_artifact_referrers(identity.managed_path)
    assert not malformed.exists()


def test_record_referrer_rejects_an_artifact_path_outside_the_projections_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    outside = tmp_path / "not-a-projection"
    outside.mkdir()
    session_home = tmp_path / "session-home"
    session_home.mkdir()

    with pytest.raises(ValueError):
        record_projected_artifact_referrer(outside, session_home)
