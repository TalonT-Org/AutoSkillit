"""Codex session lease acquisition, contention, rollback, symlink-guard, and unowned-cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit.workspace.session_skills as session_skills
from tests.workspace._helpers import (
    _BodyFailure,
    _DeletionFailure,
    _managed,
    _materialize,
    _ReleaseFailure,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


@pytest.mark.parametrize(
    ("failure_stage", "expected"),
    [
        pytest.param("prelaunch", "prelaunch failed", id="prelaunch"),
        pytest.param("setup", "setup failed", id="backend-setup"),
        pytest.param("layout", "layout failed", id="strict-layout"),
    ],
)
def test_managed_codex_home_rolls_back_every_published_owner_on_initialization_failure(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
    failure_stage: str,
    expected: str,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    if failure_stage == "prelaunch":
        codex_env.backend.ensure_pre_launch.side_effect = RuntimeError(expected)
    elif failure_stage == "setup":
        codex_env.backend.setup_session_dir.side_effect = RuntimeError(expected)
    else:
        codex_env.backend.validate_session_layout.return_value = [expected]

    with pytest.raises(RuntimeError, match=expected):
        with _managed(
            mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
        ):
            pytest.fail("initialization failure must occur before managed_session yields")

    assert not (codex_root / "0123456789abcdef").exists()
    assert "0123456789abcdef" not in mgr._session_roots
    assert "0123456789abcdef" not in mgr._session_leases
    assert "0123456789abcdef" not in mgr._session_skills_subdirs


def test_managed_codex_home_cleans_up_once_when_body_raises(
    make_session_skill_manager, codex_env, tmp_path: Path, monkeypatch
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    real_rmtree = session_skills.shutil.rmtree
    removed: list[Path] = []

    def recording_rmtree(path: Path, *args, **kwargs) -> None:
        removed.append(Path(path))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(session_skills.shutil, "rmtree", recording_rmtree)

    with pytest.raises(KeyboardInterrupt, match="stop"):
        with _managed(
            mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
        ) as managed:
            assert managed.generated_home.exists()
            raise KeyboardInterrupt("stop")

    home = codex_root / "0123456789abcdef"
    assert removed.count(home) == 1
    assert not home.exists()


def test_codex_session_requires_persistent_root_before_any_home_mutation(
    make_session_skill_manager, codex_env, tmp_path: Path
) -> None:
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider

    ephemeral_root = tmp_path / "ephemeral"
    mgr = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=ephemeral_root,
        persistent_roots={},
    )

    with pytest.raises(RuntimeError) as exc_info:
        _materialize(mgr, "0123456789abcdef", backend=codex_env.backend)

    assert str(exc_info.value) == (
        "A persistent_root is required for persistent generated-home sessions; "
        "selected_backend='codex'; configured_backend_keys=[]"
    )
    assert not ephemeral_root.exists()
    assert mgr._session_roots == {}
    assert mgr._session_leases == {}
    assert mgr._session_skills_subdirs == {}


def test_managed_codex_home_lease_acquisition_failure_precedes_home_mutation(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    def fail_acquire(
        cls: type[session_skills._SessionLease],
        path: Path,
        *,
        blocking: bool,
    ) -> session_skills._SessionLease | None:
        del cls, path, blocking
        raise OSError("lease open failed")

    monkeypatch.setattr(
        session_skills._SessionLease,
        "acquire",
        classmethod(fail_acquire),
    )

    with pytest.raises(OSError, match="lease open failed"):
        with _managed(
            mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
        ):
            pytest.fail("lease failure must precede yield")

    assert not (codex_root / "0123456789abcdef").exists()
    assert mgr._session_roots == {}
    assert mgr._session_leases == {}
    assert mgr._session_skills_subdirs == {}


def test_managed_codex_home_never_reacquires_its_lease_after_initialization(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    original_acquire = session_skills._SessionLease.acquire.__func__
    calls: list[Path] = []

    def recording_acquire(
        cls: type[session_skills._SessionLease],
        path: Path,
        *,
        blocking: bool,
    ) -> session_skills._SessionLease | None:
        calls.append(path)
        return original_acquire(cls, path, blocking=blocking)

    monkeypatch.setattr(
        session_skills._SessionLease,
        "acquire",
        classmethod(recording_acquire),
    )

    with _managed(
        mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    ):
        pass

    assert len(calls) == 1


def test_unowned_cleanup_refuses_a_contended_generated_home(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    owner = make_session_skill_manager(codex_root=codex_root)
    contender = make_session_skill_manager(codex_root=codex_root)
    _materialize(
        owner, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
    )

    assert contender.cleanup_session("0123456789abcdef") is False
    assert (codex_root / "0123456789abcdef").is_dir()
    assert owner.cleanup_session("0123456789abcdef") is True


def test_session_lease_rejects_non_lock_path(tmp_path: Path) -> None:
    invalid_path = tmp_path / ".session-leases" / "lease"

    with pytest.raises(ValueError, match=r"\.lock suffix"):
        session_skills._SessionLease.acquire(invalid_path, blocking=True)

    assert not invalid_path.parent.exists()


def test_session_lease_refuses_symlinked_lock_directory(tmp_path: Path) -> None:
    codex_root = tmp_path / "codex-sessions"
    codex_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    lock_root = codex_root / ".session-leases"
    lock_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        session_skills._SessionLease.acquire(
            lock_root / "0123456789abcdef.lock",
            blocking=True,
        )

    assert list(outside.iterdir()) == []


def test_session_lease_refuses_symlinked_lock_file(tmp_path: Path) -> None:
    lock_root = tmp_path / "codex-sessions" / ".session-leases"
    lock_root.mkdir(parents=True)
    outside = tmp_path / "outside.lock"
    outside.write_text("sentinel", encoding="utf-8")
    lock_path = lock_root / "0123456789abcdef.lock"
    lock_path.symlink_to(outside)

    with pytest.raises(OSError):
        session_skills._SessionLease.acquire(lock_path, blocking=True)

    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_stale_cleanup_never_treats_the_external_lock_directory_as_a_home(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    lock_root = codex_root / ".session-leases"
    lock_root.mkdir(parents=True)
    (lock_root / "orphan.lock").write_text("diagnostic", encoding="utf-8")
    mgr = make_session_skill_manager(
        ephemeral_root=tmp_path / "ephemeral",
        codex_root=codex_root,
    )

    assert mgr.cleanup_stale(max_age_seconds=0) == 0
    assert (lock_root / "orphan.lock").is_file()


def test_unowned_cleanup_reclaims_a_dead_owner_home(
    make_session_skill_manager,
    tmp_path: Path,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    generated_home = codex_root / "0123456789abcdef"
    generated_home.mkdir(parents=True)
    (generated_home / "stale").write_text("orphan", encoding="utf-8")
    mgr = make_session_skill_manager(codex_root=codex_root)

    assert mgr.cleanup_session("0123456789abcdef") is True
    assert not generated_home.exists()


def test_managed_cleanup_preserves_a_lone_deletion_failure(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    def fail_delete(path: Path) -> bool:
        del path
        raise _DeletionFailure("delete failed")

    with pytest.raises(_DeletionFailure, match="delete failed"):
        with _managed(
            mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
        ):
            monkeypatch.setattr(
                session_skills,
                "_remove_and_verify",
                fail_delete,
            )


def test_managed_cleanup_preserves_a_lone_release_failure(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    real_release = session_skills._SessionLease.release

    def fail_after_release(lease: session_skills._SessionLease) -> None:
        real_release(lease)
        raise _ReleaseFailure("release failed")

    with pytest.raises(_ReleaseFailure, match="release failed"):
        with _managed(
            mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
        ):
            monkeypatch.setattr(
                session_skills._SessionLease,
                "release",
                fail_after_release,
            )


def test_managed_cleanup_groups_body_deletion_and_release_failures_in_order(
    make_session_skill_manager,
    codex_env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    real_release = session_skills._SessionLease.release

    def fail_delete(path: Path) -> bool:
        del path
        raise _DeletionFailure("delete failed")

    def fail_after_release(lease: session_skills._SessionLease) -> None:
        real_release(lease)
        raise _ReleaseFailure("release failed")

    with pytest.raises(BaseExceptionGroup) as caught:
        with _managed(
            mgr, "0123456789abcdef", backend=codex_env.backend, names=frozenset({"make-arch-diag"})
        ):
            monkeypatch.setattr(session_skills, "_remove_and_verify", fail_delete)
            monkeypatch.setattr(
                session_skills._SessionLease,
                "release",
                fail_after_release,
            )
            raise _BodyFailure("body failed")

    assert [type(error) for error in caught.value.exceptions] == [
        _BodyFailure,
        _DeletionFailure,
        _ReleaseFailure,
    ]
