"""Tests for project-local skill override detection and enforcement (T-OVR-001..011).

Tests must FAIL against the current codebase and pass after Part B implementation.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# T-OVR-001..006: detect_project_local_overrides() — pure detection function
# ---------------------------------------------------------------------------


def test_detect_project_local_overrides_empty(tmp_path):
    """T-OVR-001: Returns empty frozenset when no override dirs exist."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset()


def test_detect_project_local_overrides_claude_skills(tmp_path):
    """T-OVR-002: Detects skill in .claude/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# review-pr")
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset({"review-pr"})


def test_detect_project_local_overrides_autoskillit_skills(tmp_path):
    """T-OVR-003: Detects skill in .autoskillit/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".autoskillit" / "skills" / "open-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# open-pr")
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset({"open-pr"})


def test_detect_project_local_overrides_union(tmp_path):
    """T-OVR-004: Returns union from both .claude/skills/ and .autoskillit/skills/."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/review-pr", "review-pr"),
        (".autoskillit/skills/open-pr", "open-pr"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# skill")
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset({"review-pr", "open-pr"})


def test_detect_project_local_overrides_ignores_missing_skill_md(tmp_path):
    """T-OVR-005: Directories without SKILL.md are ignored."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    (tmp_path / ".claude" / "skills" / "review-pr").mkdir(parents=True)
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset()


def test_detect_project_local_overrides_missing_dirs_no_crash(tmp_path):
    """T-OVR-006: Missing parent directories do not raise."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    result = detect_project_local_overrides(tmp_path / "nonexistent")
    assert result == frozenset()


# ---------------------------------------------------------------------------
# T-OVR-007..011: init_session() — project_dir override filtering
# ---------------------------------------------------------------------------


def test_init_session_no_override_when_project_dir_none(tmp_path):
    """T-OVR-007: init_session() with project_dir=None performs no override filtering."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-001", project_dir=None)
    assert (skills_dir / "investigate" / "SKILL.md").exists()


def test_init_session_excludes_overridden_skill(tmp_path):
    """T-OVR-008: init_session() excludes bundled skill when project-local override exists."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom investigate")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-002", project_dir=project_dir)
    assert not (skills_dir / "investigate" / "SKILL.md").exists()


def test_init_session_includes_non_overridden_skills(tmp_path):
    """T-OVR-009: Non-overridden skills are still included."""
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # Override "investigate" only
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-003", project_dir=project_dir)
    # "make-plan" must still be present
    assert (skills_dir / "make-plan" / "SKILL.md").exists()


def test_init_session_subset_and_override_compose(tmp_path):
    """T-OVR-010: Subset disable and override compose independently."""
    from autoskillit.config import AutomationConfig, SubsetsConfig
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # Project-local override for "review-pr"
    override = project_dir / ".claude" / "skills" / "review-pr"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")
    # Config disables "github" subset (which covers open-pr)
    config = AutomationConfig(subsets=SubsetsConfig(disabled=["github"]))
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    skills_dir = mgr.init_session("sess-004", config=config, project_dir=project_dir)
    # "open-pr" absent due to subset; "review-pr" absent due to override
    assert not (skills_dir / "review-pr" / "SKILL.md").exists()
    assert not (skills_dir / "open-pr" / "SKILL.md").exists()


def test_init_session_logs_override_skip(tmp_path):
    """T-OVR-011: Debug log emitted for each overridden skill skipped."""
    import structlog.testing

    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    override = project_dir / ".claude" / "skills" / "investigate"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text("# custom")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), tmp_path / "ephemeral")
    with structlog.testing.capture_logs() as logs:
        mgr.init_session("sess-005", project_dir=project_dir)
    skip_events = [e for e in logs if e.get("event") == "init_session_override_skip"]
    assert any(e.get("skill") == "investigate" for e in skip_events)
