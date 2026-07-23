"""Tests for validate_session_exists() and cleanup_stale() structured logging."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _materialize(manager, session_id: str) -> None:
    from pathlib import Path

    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace import DefaultSkillResolver

    project_root = Path.cwd()
    catalog = DefaultSkillResolver().list_effective(
        project_root,
        SkillExecutionRole.SESSION,
    )
    context = manager._provider.catalog_projection_context(catalog, project_root)
    manager.init_session(session_id, catalog, context)


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
    _materialize(mgr, "sess-stale")

    session_dir = mgr._session_roots["sess-stale"] / "sess-stale"  # type: ignore[attr-defined]
    assert session_dir.is_dir()

    # Backdate access time so the session qualifies as stale.
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
