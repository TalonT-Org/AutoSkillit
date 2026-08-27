"""Tests for validate_session_exists() and cleanup_stale() structured logging."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    VANISHED_ERRORS,
    BackendConventions,
    ClaudeDirectoryConventions,
    PreLaunchReadiness,
    SkillSource,
    pkg_root,
)
from tests.fakes import adapt_test_skill_semantics
from tests.workspace._helpers import _CODEX_CAPABILITIES, _write_project_skill_override

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _codex_backend() -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = _CODEX_CAPABILITIES
    backend.conventions = BackendConventions(
        skills_subdir=ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR,
        profile_skills_source=None,
    )
    backend.ensure_pre_launch.return_value = PreLaunchReadiness((), {})
    backend.setup_session_dir.return_value = None
    backend.validate_session_layout.return_value = []
    backend.adapt_skill_semantics.side_effect = adapt_test_skill_semantics
    return backend


def _catalog_context(manager, *, backend=None):
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace import DefaultSkillResolver, EffectiveSkillCatalog

    project_root = manager.ephemeral_root
    catalog = DefaultSkillResolver().list_effective(
        project_root,
        SkillExecutionRole.SESSION,
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(member for member in catalog.skills if not member.exploration_vectors),
        execution_role=SkillExecutionRole.SESSION,
    )
    context = manager._provider.catalog_projection_context(
        catalog,
        project_root,
        backend=backend,
        durable_scripts_root=pkg_root(),
    )
    return catalog, context


def _materialize(manager, session_id: str, *, backend=None):
    catalog, context = _catalog_context(manager, backend=backend)
    return manager.init_session(session_id, catalog, context)


def _managed(manager, session_id: str, *, backend):
    from autoskillit.workspace import compile_session_skill_catalog

    catalog, context = _catalog_context(manager, backend=backend)
    return manager.managed_session(
        session_id,
        compile_session_skill_catalog(catalog, backend),
        context,
    )


def test_catalog_context_uses_manager_root_when_cwd_has_project_override(
    make_session_skill_manager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = make_session_skill_manager()
    bundled = manager._provider.resolver.resolve("open-kitchen")
    assert bundled is not None
    foreign_project = tmp_path / "foreign-project"
    _write_project_skill_override(
        foreign_project,
        "open-kitchen",
        bundled.canonical_content,
    )
    monkeypatch.chdir(foreign_project)

    catalog, context = _catalog_context(manager)

    open_kitchen = next(skill for skill in catalog.skills if skill.name == "open-kitchen")
    assert context.cwd == manager.ephemeral_root.resolve()
    assert open_kitchen.source is SkillSource.BUNDLED


def test_validate_session_exists_true_for_live_session(make_session_skill_manager) -> None:
    """validate_session_exists returns True for a freshly created session."""
    mgr = make_session_skill_manager()
    _materialize(mgr, "sess-live")

    assert mgr.validate_session_exists("sess-live") is True


def test_validate_session_exists_false_after_delete(make_session_skill_manager) -> None:
    """validate_session_exists returns False after cleanup_session removes the dir."""
    mgr = make_session_skill_manager()
    _materialize(mgr, "sess-deleted")

    assert mgr.cleanup_session("sess-deleted") is True
    assert mgr.validate_session_exists("sess-deleted") is False


def test_validate_session_exists_false_for_unknown(make_session_skill_manager) -> None:
    """validate_session_exists returns False for an unknown session_id."""
    mgr = make_session_skill_manager()
    assert mgr.validate_session_exists("nonexistent-session") is False


def test_cleanup_stale_emits_log_event(make_session_skill_manager, monkeypatch) -> None:
    """cleanup_stale emits a structured log event when removing stale dirs."""
    import autoskillit.workspace.session_skill_manager as skills_mod

    mgr = make_session_skill_manager()
    session_dir = mgr.ephemeral_root / "sess-stale"
    session_dir.mkdir(parents=True)
    (session_dir / "orphaned-session-marker").touch()

    # Backdate an unowned generated home so the session qualifies as stale.
    old_time = 1_000_000.0
    os.utime(session_dir, (old_time, old_time))

    # Capture logger.warning calls on the module-level logger.
    captured = MagicMock()
    monkeypatch.setattr(skills_mod, "logger", captured)

    removed = mgr.cleanup_stale(max_age_seconds=1)

    assert removed >= 1
    assert not session_dir.exists()

    # Find the cleanup_stale_removed event.
    matching = [
        call
        for call in captured.warning.call_args_list
        if call.args and call.args[0] == "cleanup_stale_removed"
    ]
    assert len(matching) == 1
    kwargs = matching[0].kwargs
    assert "path" in kwargs
    assert "age_seconds" in kwargs
    assert kwargs["path"] == str(session_dir)


def test_cleanup_stale_does_not_remove_a_leased_generated_home(
    make_session_skill_manager, tmp_path: Path
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)
    with _managed(mgr, "0123456789abcdef", backend=_codex_backend()) as managed:
        old_time = 1_000_000.0
        os.utime(managed.generated_home, (old_time, old_time))

        assert mgr.cleanup_stale(max_age_seconds=1) == 0
        assert managed.generated_home.is_dir()
        assert "0123456789abcdef" in mgr._session_leases

    assert not (codex_root / "0123456789abcdef").exists()


def test_init_session_retains_lease_until_cleanup_session(
    make_session_skill_manager, tmp_path: Path
) -> None:
    codex_root = tmp_path / "persistent" / "codex-sessions"
    mgr = make_session_skill_manager(codex_root=codex_root)

    generated_home = _materialize(
        mgr,
        "0123456789abcdef",
        backend=_codex_backend(),
    )

    assert Path(str(generated_home)).is_dir()
    assert "0123456789abcdef" in mgr._session_leases
    assert mgr.cleanup_session("0123456789abcdef") is True
    assert "0123456789abcdef" not in mgr._session_leases
    assert not Path(str(generated_home)).exists()


def test_cleanup_stale_continues_past_a_candidate_that_vanishes_during_observation(
    make_session_skill_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = make_session_skill_manager(codex_root=None)
    root = mgr.ephemeral_root
    stale_a = root / "stale-a"
    stale_b = root / "stale-b"
    live_c = root / "live-c"
    for candidate in (stale_a, stale_b, live_c):
        candidate.mkdir()
    old_time = 1_000_000.0
    os.utime(stale_a, (old_time, old_time))
    os.utime(stale_b, (old_time, old_time))

    original_scandir = os.scandir

    class VanishingScandir:
        def __init__(self, scanner) -> None:
            self._scanner = scanner
            self._vanished = False

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self._scanner)
            if entry.name == stale_a.name and not self._vanished:
                self._vanished = True
                stale_a.rmdir()
            return entry

        def close(self) -> None:
            self._scanner.close()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.close()

    def vanish_during_observation(path):
        scanner = original_scandir(path)
        if path == root or path == str(root):
            return VanishingScandir(scanner)
        return scanner

    monkeypatch.setattr(os, "scandir", vanish_during_observation)

    assert mgr.cleanup_stale(max_age_seconds=1) == 1
    assert not stale_b.exists()
    assert live_c.is_dir()


@pytest.mark.parametrize("vanished_error", VANISHED_ERRORS)
def test_cleanup_stale_continues_when_the_post_lease_observation_finds_the_candidate_gone(
    make_session_skill_manager,
    monkeypatch: pytest.MonkeyPatch,
    vanished_error: type[OSError],
) -> None:
    import autoskillit.workspace.session_skill_manager as skills_mod

    mgr = make_session_skill_manager(codex_root=None)
    root = mgr.ephemeral_root
    stale = root / "stale"
    stale.mkdir()
    old_time = 1_000_000.0
    os.utime(stale, (old_time, old_time))
    original_acquire = skills_mod._SessionLease.acquire
    mutated = False

    def acquire_after_preliminary_observation(cls, lock_path, *, blocking):
        nonlocal mutated
        lease = original_acquire(lock_path, blocking=blocking)
        if lease is not None and lock_path.name == "stale.lock" and not mutated:
            mutated = True
            if vanished_error is FileNotFoundError:
                stale.rmdir()
            else:
                moved_root = root.with_name(f"{root.name}-moved")
                root.rename(moved_root)
                root.write_text("replaced by a regular file", encoding="utf-8")
        return lease

    monkeypatch.setattr(
        skills_mod._SessionLease,
        "acquire",
        classmethod(acquire_after_preliminary_observation),
    )

    assert mgr.cleanup_stale(max_age_seconds=1) == 0
    assert mutated


def test_cleanup_stale_post_lease_removal_survives_an_intermediate_component_replacement(
    make_session_skill_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.workspace.session_skill_manager as skills_mod

    mgr = make_session_skill_manager(codex_root=None)
    stale = mgr.ephemeral_root / "stale"
    stale.mkdir()
    old_time = 1_000_000.0
    os.utime(stale, (old_time, old_time))

    def removal_after_intermediate_replacement(path: Path) -> bool:
        assert path == stale
        raise NotADirectoryError("injected intermediate component replacement")

    monkeypatch.setattr(
        skills_mod,
        "_remove_and_verify",
        removal_after_intermediate_replacement,
    )

    assert mgr.cleanup_stale(max_age_seconds=1) == 0
    assert stale.is_dir()


def test_cleanup_stale_removes_remaining_candidates_when_one_removal_fails(
    make_session_skill_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.workspace.session_skill_manager as skills_mod

    mgr = make_session_skill_manager(codex_root=None)
    first = mgr.ephemeral_root / "first"
    second = mgr.ephemeral_root / "second"
    for candidate in (first, second):
        candidate.mkdir()
        os.utime(candidate, (1_000_000.0, 1_000_000.0))
    original_remove_and_verify = skills_mod._remove_and_verify

    def fail_the_first_removal(path: Path) -> bool:
        if path == first:
            raise RuntimeError("injected")
        return original_remove_and_verify(path)

    monkeypatch.setattr(skills_mod, "_remove_and_verify", fail_the_first_removal)

    with pytest.raises(RuntimeError, match="injected"):
        mgr.cleanup_stale(max_age_seconds=1)

    assert not second.exists()


def test_cleanup_stale_skips_a_symlink_under_a_candidate_root(
    make_session_skill_manager,
) -> None:
    mgr = make_session_skill_manager(codex_root=None)
    target = mgr.ephemeral_root / "target"
    target.mkdir()
    symlink = mgr.ephemeral_root / "symlink"
    symlink.symlink_to(target, target_is_directory=True)
    os.utime(symlink, (1_000_000.0, 1_000_000.0), follow_symlinks=False)

    assert mgr.cleanup_stale(max_age_seconds=1) == 0
    assert symlink.is_symlink()


@pytest.mark.parametrize(
    "first_root_state",
    ("removed", "replaced-by-file"),
    ids=("root-removed-required-non-regression", "root-replaced-by-file"),
)
def test_cleanup_stale_continues_to_the_next_root_when_one_root_vanishes(
    make_session_skill_manager,
    tmp_path: Path,
    first_root_state: str,
) -> None:
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    mgr = make_session_skill_manager(
        ephemeral_root=first_root,
        codex_root=second_root,
    )
    first_root.mkdir()
    second_root.mkdir()
    stale = second_root / "stale"
    stale.mkdir()
    os.utime(stale, (1_000_000.0, 1_000_000.0))

    if first_root_state == "removed":
        first_root.rmdir()
    else:
        first_root.rmdir()
        first_root.write_text("replaced by a regular file", encoding="utf-8")

    assert mgr.cleanup_stale(max_age_seconds=1) == 1
    assert not stale.exists()


def test_cleanup_stale_propagates_permission_error_from_a_root(
    make_session_skill_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = make_session_skill_manager(codex_root=None)
    root = mgr.ephemeral_root
    original_scandir = os.scandir

    def deny_root_scan(path):
        if path == root or path == str(root):
            raise PermissionError("injected root scan failure")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", deny_root_scan)

    with pytest.raises(PermissionError, match="injected root scan failure"):
        mgr.cleanup_stale(max_age_seconds=1)
