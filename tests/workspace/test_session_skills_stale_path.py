"""Tests for validate_session_exists() and cleanup_stale() structured logging."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ClaudeDirectoryConventions, pkg_root
from tests.fakes import adapt_test_skill_semantics
from tests.workspace._helpers import _CODEX_CAPABILITIES

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _codex_backend() -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = _CODEX_CAPABILITIES
    backend.conventions.skills_subdir = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    backend.ensure_pre_launch.return_value = []
    backend.validate_session_layout.return_value = []
    backend.adapt_skill_semantics.side_effect = adapt_test_skill_semantics
    return backend


def _catalog_context(manager, *, backend=None):
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace import DefaultSkillResolver, EffectiveSkillCatalog

    project_root = Path.cwd()
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
    catalog, context = _catalog_context(manager, backend=backend)
    return manager.managed_session(session_id, catalog, context)


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
    import autoskillit.workspace.session_skills as skills_mod

    mgr = make_session_skill_manager()
    session_dir = mgr._root / "sess-stale"  # type: ignore[attr-defined]
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
