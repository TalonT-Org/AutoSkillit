"""Symmetric delivery contract tests for project-local skill overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types._type_backend import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("search_dir", ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS)
def test_project_local_skill_delivered_from_each_search_dir(
    tmp_path: Path, search_dir: str
) -> None:
    """Skills placed in any search dir are copied into the ephemeral session dir."""
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_root = tmp_path / "project"
    skill_dir = project_root / search_dir / "investigate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"# PROJECT LOCAL from {search_dir}")

    backend = get_backend("claude-code")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root=tmp_path / "eph")
    result = mgr.init_session(
        "sess-delivery", cook_session=False, project_dir=project_root, backend=backend
    )

    delivered = result / ".claude" / "skills" / "investigate" / "SKILL.md"
    assert Path(delivered).exists(), (
        f"project-local skill from {search_dir} must be delivered into ephemeral dir"
    )
    assert "# PROJECT LOCAL" in Path(delivered).read_text()


def test_no_channel_collision_when_both_dirs_have_same_skill(tmp_path: Path) -> None:
    """First-match-wins: .claude/skills/ takes precedence over .autoskillit/skills/."""
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    project_root = tmp_path / "project"
    claude_skill = project_root / ".claude" / "skills" / "foo"
    claude_skill.mkdir(parents=True)
    (claude_skill / "SKILL.md").write_text("# FROM CLAUDE")

    as_skill = project_root / ".autoskillit" / "skills" / "foo"
    as_skill.mkdir(parents=True)
    (as_skill / "SKILL.md").write_text("# FROM AUTOSKILLIT")

    backend = get_backend("claude-code")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root=tmp_path / "eph")
    result = mgr.init_session(
        "sess-collision", cook_session=False, project_dir=project_root, backend=backend
    )

    delivered = result / ".claude" / "skills" / "foo" / "SKILL.md"
    content = Path(delivered).read_text()
    assert "# FROM CLAUDE" in content, ".claude/skills/ must win (first in tuple order)"
    assert "# FROM AUTOSKILLIT" not in content


def test_project_dir_not_cwd_detects_overrides(tmp_path: Path) -> None:
    """project_dir (kitchen root) is scanned for overrides, not the worktree CWD."""
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )

    kitchen_root = tmp_path / "kitchen"
    skill_dir = kitchen_root / ".autoskillit" / "skills" / "foo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# KITCHEN LOCAL")

    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()

    backend = get_backend("claude-code")
    mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root=tmp_path / "eph")
    result = mgr.init_session(
        "sess-worktree", cook_session=False, project_dir=kitchen_root, backend=backend
    )

    delivered = result / ".claude" / "skills" / "foo" / "SKILL.md"
    assert Path(delivered).exists(), (
        "override from kitchen root must be delivered even when CWD is a worktree"
    )
    assert "# KITCHEN LOCAL" in Path(delivered).read_text()
