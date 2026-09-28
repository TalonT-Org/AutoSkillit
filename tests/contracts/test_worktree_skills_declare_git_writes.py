"""Every bundled worktree skill declares its git metadata writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import WORKTREE_SKILLS
from autoskillit.core.io import load_yaml
from autoskillit.workspace.skill_capabilities._semantic_plan import (
    parse_skill_semantic_plan,
)

SKILLS_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "skills_extended"

pytestmark = [pytest.mark.small]


def test_worktree_skills_declare_git_metadata_writes() -> None:
    skill_paths = {path.parent.name: path for path in SKILLS_DIR.rglob("SKILL.md")}
    missing: list[str] = []

    for name in sorted(WORKTREE_SKILLS):
        path = skill_paths[name]
        content = path.read_text(encoding="utf-8")
        data = load_yaml(content.split("---", 2)[1])
        plan, _diagnostics = parse_skill_semantic_plan(
            data,
            path=path,
            content=content,
            uses_capabilities=frozenset(data.get("uses_capabilities", ())),
        )
        if plan is None or not plan.git_metadata_writes:
            missing.append(name)

    assert not missing, "Worktree skills missing git_metadata_writes: " + ", ".join(missing)
