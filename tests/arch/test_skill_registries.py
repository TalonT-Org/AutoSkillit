"""Architectural tests for skill registry completeness."""

from __future__ import annotations

import pathlib

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.execution.clone_guard import CLONE_COMMIT_SKILLS, WORKTREE_SKILLS
from autoskillit.recipe.rules.rules_worktree import _WORKTREE_MODIFYING_SKILLS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_worktree_skills_complete():
    """Every entry in _WORKTREE_MODIFYING_SKILLS must appear in WORKTREE_SKILLS."""
    missing = _WORKTREE_MODIFYING_SKILLS - WORKTREE_SKILLS
    assert not missing, (
        f"WORKTREE_SKILLS is missing skills that _WORKTREE_MODIFYING_SKILLS declares: {missing}"
    )


def _load_skill_contracts() -> dict:
    import autoskillit.recipe

    contracts_path = pathlib.Path(autoskillit.recipe.__file__).parent / "skill_contracts.yaml"
    data = load_yaml(contracts_path)
    return data.get("skills", data)


def test_clone_commit_skills_have_write_behavior_conditional():
    """Every skill in CLONE_COMMIT_SKILLS must have write_behavior: conditional."""
    assert CLONE_COMMIT_SKILLS, "CLONE_COMMIT_SKILLS must not be empty"
    contracts = _load_skill_contracts()
    for skill_name in CLONE_COMMIT_SKILLS:
        contract = contracts.get(skill_name, {})
        assert contract.get("write_behavior") == "conditional", (
            f"{skill_name} in CLONE_COMMIT_SKILLS must have write_behavior: conditional"
        )


def test_clone_commit_skills_not_read_only():
    """No skill in CLONE_COMMIT_SKILLS should have read_only: true."""
    assert CLONE_COMMIT_SKILLS, "CLONE_COMMIT_SKILLS must not be empty"
    contracts = _load_skill_contracts()
    for skill_name in CLONE_COMMIT_SKILLS:
        contract = contracts.get(skill_name, {})
        assert contract.get("read_only") is not True, (
            f"{skill_name} in CLONE_COMMIT_SKILLS must not have read_only: true"
        )
