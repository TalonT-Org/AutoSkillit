"""Symmetric delivery contract tests for project-local skill overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS, SkillExecutionRole

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _project_skill_document(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: Project-local {name} fixture.\n---\n{body}\n"


def _materialize_project(
    project_root: Path,
    ephemeral_root: Path,
    session_id: str,
):
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import DefaultSkillResolver
    from autoskillit.workspace.session_skills import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
    )
    from tests.contracts._projection_helpers import non_exploration_catalog

    backend = get_backend("claude-code")
    provider = SkillsDirectoryProvider()
    catalog = non_exploration_catalog(
        DefaultSkillResolver().list_effective(
            project_root,
            SkillExecutionRole.SESSION,
        )
    )
    context = provider.catalog_projection_context(
        catalog,
        project_root,
        backend=backend,
    )
    manager = DefaultSessionSkillManager(provider, ephemeral_root=ephemeral_root)
    return manager.init_session(session_id, catalog, context)


@pytest.mark.parametrize("search_dir", ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS)
def test_project_local_skill_delivered_from_each_search_dir(
    tmp_path: Path, search_dir: str
) -> None:
    """Skills placed in any search dir are copied into the ephemeral session dir."""
    project_root = tmp_path / "project"
    skill_name = "local-delivery-fixture"
    skill_dir = project_root / search_dir / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        _project_skill_document(skill_name, f"# PROJECT LOCAL from {search_dir}")
    )

    result = _materialize_project(project_root, tmp_path / "eph", "sess-delivery")

    delivered = result / ".claude" / "skills" / skill_name / "SKILL.md"
    assert Path(delivered).exists(), (
        f"project-local skill from {search_dir} must be delivered into ephemeral dir"
    )
    assert "# PROJECT LOCAL" in Path(delivered).read_text()


def test_no_channel_collision_when_both_dirs_have_same_skill(tmp_path: Path) -> None:
    """First-match-wins: .claude/skills/ takes precedence over .autoskillit/skills/."""
    project_root = tmp_path / "project"
    claude_skill = project_root / ".claude" / "skills" / "foo"
    claude_skill.mkdir(parents=True)
    (claude_skill / "SKILL.md").write_text(_project_skill_document("foo", "# FROM CLAUDE"))

    as_skill = project_root / ".autoskillit" / "skills" / "foo"
    as_skill.mkdir(parents=True)
    (as_skill / "SKILL.md").write_text(_project_skill_document("foo", "# FROM AUTOSKILLIT"))

    result = _materialize_project(project_root, tmp_path / "eph", "sess-collision")

    delivered = result / ".claude" / "skills" / "foo" / "SKILL.md"
    content = Path(delivered).read_text()
    assert "# FROM CLAUDE" in content, ".claude/skills/ must win (first in tuple order)"
    assert "# FROM AUTOSKILLIT" not in content


def test_project_dir_not_cwd_detects_overrides(tmp_path: Path) -> None:
    """project_dir (kitchen root) is scanned for overrides, not the worktree CWD."""
    kitchen_root = tmp_path / "kitchen"
    skill_dir = kitchen_root / ".autoskillit" / "skills" / "foo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_project_skill_document("foo", "# KITCHEN LOCAL"))

    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()

    result = _materialize_project(kitchen_root, tmp_path / "eph", "sess-worktree")

    delivered = result / ".claude" / "skills" / "foo" / "SKILL.md"
    assert Path(delivered).exists(), (
        "override from kitchen root must be delivered even when CWD is a worktree"
    )
    assert "# KITCHEN LOCAL" in Path(delivered).read_text()


def test_delivery_is_backend_independent_across_search_dirs(tmp_path: Path) -> None:
    """Effective source discovery is complete before backend selection."""
    project_root = tmp_path / "project"
    foreign_skill_dir = project_root / ".codex" / "skills" / "foreign-skill"
    foreign_skill_dir.mkdir(parents=True)
    (foreign_skill_dir / "SKILL.md").write_text(
        _project_skill_document("foreign-skill", "# FOREIGN SKILL")
    )

    result = _materialize_project(project_root, tmp_path / "eph", "sess-foreign")

    delivered = result / ".claude" / "skills" / "foreign-skill" / "SKILL.md"
    assert Path(delivered).exists()
    assert "# FOREIGN SKILL" in Path(delivered).read_text()


@pytest.mark.parametrize("search_dir", ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS)
def test_backend_scoped_delivery_includes_convention_dirs(tmp_path: Path, search_dir: str) -> None:
    """claude-code backend delivers skills from each convention dir individually."""
    project_root = tmp_path / "project"
    skill_name = f"conv-skill-{search_dir.replace('/', '-').replace('.', '-')}"
    skill_dir = project_root / search_dir / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        _project_skill_document(skill_name, f"# CONVENTION SKILL from {search_dir}")
    )

    result = _materialize_project(project_root, tmp_path / "eph", "sess-conv")

    delivered = result / ".claude" / "skills" / skill_name / "SKILL.md"
    assert Path(delivered).exists(), (
        f"claude-code backend must deliver skill from convention dir {search_dir}"
    )
    assert "# CONVENTION SKILL" in Path(delivered).read_text()
