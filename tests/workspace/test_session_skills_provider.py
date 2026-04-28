"""Phase 2 tests: session_skills module — provider and core manager."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
import yaml

from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def test_resolve_ephemeral_root_returns_writable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.workspace.session_skills as ss

    monkeypatch.setattr(ss, "_CANDIDATE_ROOTS", [tmp_path])
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


def test_provider_injects_disable_model_invocation_for_tier2() -> None:
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("open-kitchen", gated=True)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_provider_does_not_inject_for_cook_session() -> None:
    # Use mermaid (skills_extended/, no flag at rest) to verify that gated=False
    # returns unmodified content without injecting disable-model-invocation.
    # open-kitchen and close-kitchen carry disable-model-invocation: true in their source
    # (human-only skills), so they cannot be used to assert "flag not present".
    provider = SkillsDirectoryProvider()
    content = provider.get_skill_content("mermaid", gated=False)
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "Content must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_session_skill_manager_creates_ephemeral_dir(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("test-session-abc", cook_session=False)
    assert session_path.exists()
    assert session_path.is_dir()
    skill_files = list(session_path.glob(".claude/skills/*/SKILL.md"))
    assert len(skill_files) > 0


def test_session_manager_injects_disable_for_tier2(tmp_path: Path) -> None:
    """Non-cook init_session injects disable-model-invocation for tier2 skills."""
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session("test-session-xyz", cook_session=False, config=config)
    mermaid_md = session_path / ".claude" / "skills" / "mermaid" / "SKILL.md"
    assert mermaid_md.exists()
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True


def test_session_manager_no_flag_for_cook_session(tmp_path: Path) -> None:
    """Cook session does not inject disable-model-invocation even for tier2 skills."""
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    session_path = mgr.init_session("cook-session-123", cook_session=True, config=config)
    mermaid_md = session_path / ".claude" / "skills" / "mermaid" / "SKILL.md"
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is not True


def test_activate_skill_deps_removes_flag(tmp_path: Path) -> None:
    from tests._helpers import make_skills_config, make_test_config

    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    config = make_test_config(
        skills=make_skills_config(
            tier1=["open-kitchen", "close-kitchen"],
            tier2=["mermaid"],
            tier3=[],
        )
    )
    mgr.init_session("session-toggle", cook_session=False, config=config)
    result = mgr.activate_skill_deps("session-toggle", "mermaid")
    assert result is True
    mermaid_md = tmp_path / "session-toggle" / ".claude" / "skills" / "mermaid" / "SKILL.md"
    content = mermaid_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert "disable-model-invocation" not in fm or fm.get("disable-model-invocation") is not True


def test_cleanup_stale_removes_old_dirs(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    stale_dir = tmp_path / "stale-session"
    stale_dir.mkdir()
    os.utime(stale_dir, (time.time() - 90000, time.time() - 90000))  # 25h old
    fresh_dir = tmp_path / "fresh-session"
    fresh_dir.mkdir()
    count = mgr.cleanup_stale(max_age_seconds=86400)
    assert count == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()


def test_tier2_skills_constant_removed() -> None:
    """TIER2_SKILLS no longer exported from workspace (superseded by config)."""
    import autoskillit.workspace as ws

    assert not hasattr(ws, "TIER2_SKILLS")
