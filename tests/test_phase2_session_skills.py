"""Phase 2 tests: session_skills module (resolver, provider, manager)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
import yaml

from autoskillit.workspace.session_skills import (
    TIER2_SKILLS,
    SessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)


def test_resolve_ephemeral_root_returns_writable_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = resolve_ephemeral_root()
    assert root.exists()
    assert root.is_dir()
    test_file = root / "write_test.tmp"
    test_file.write_text("ok")
    test_file.unlink()


def test_resolve_ephemeral_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [Path("/nonexistent"), tmp_path])
    root = ss.resolve_ephemeral_root()
    assert root.exists()


def test_skills_directory_provider_lists_all_skills() -> None:
    provider = SkillsDirectoryProvider()
    skills = provider.list_skills()
    names = {s.name for s in skills}
    assert "open-kitchen" in names
    assert "close-kitchen" in names
    assert "implement-worktree" in names
    assert "sous-chef" not in names  # internal, excluded


def test_skills_directory_provider_tier2_classification() -> None:
    assert "open-kitchen" in TIER2_SKILLS
    assert "close-kitchen" in TIER2_SKILLS
    assert "implement-worktree" not in TIER2_SKILLS


def test_provider_injects_disable_model_invocation_for_tier2() -> None:
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("open-kitchen", tier2_gated=True)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_provider_does_not_inject_for_cook_session() -> None:
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("open-kitchen", tier2_gated=False)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_session_skill_manager_creates_ephemeral_dir(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = SessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-session-abc", cook_session=False)
    assert session_path.exists()
    assert session_path.is_dir()
    skill_files = list(session_path.glob("*/SKILL.md"))
    assert len(skill_files) > 0


def test_session_manager_injects_disable_for_tier2(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = SessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-session-xyz", cook_session=False)
    open_kitchen_md = session_path / "open-kitchen" / "SKILL.md"
    assert open_kitchen_md.exists()
    content = open_kitchen_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_session_manager_no_flag_for_cook_session(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = SessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("cook-session-123", cook_session=True)
    open_kitchen_md = session_path / "open-kitchen" / "SKILL.md"
    content = open_kitchen_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_activate_tier2_removes_flag(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = SessionSkillManager(provider, ephemeral_root=tmp_path)
    mgr.init_session("session-toggle", cook_session=False)
    result = mgr.activate_tier2("session-toggle", "open-kitchen")
    assert result is True
    open_kitchen_md = tmp_path / "session-toggle" / "open-kitchen" / "SKILL.md"
    content = open_kitchen_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert "disable-model-invocation" not in fm or fm.get("disable-model-invocation") is not True


def test_cleanup_stale_removes_old_dirs(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = SessionSkillManager(provider, ephemeral_root=tmp_path)
    stale_dir = tmp_path / "stale-session"
    stale_dir.mkdir()
    os.utime(stale_dir, (time.time() - 90000, time.time() - 90000))  # 25h old
    fresh_dir = tmp_path / "fresh-session"
    fresh_dir.mkdir()
    count = mgr.cleanup_stale(max_age_seconds=86400)
    assert count == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()
