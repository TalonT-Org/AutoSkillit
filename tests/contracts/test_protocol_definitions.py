"""Tests for Protocol definitions in core/_type_protocols_*.py shards.

REQ-PROTO-007: SkillLister Protocol must live in core/_type_protocols_workspace.py.
"""

from __future__ import annotations

import inspect


def test_github_fetcher_has_update_issue_body() -> None:
    from autoskillit.core.types._type_protocols_github import GitHubFetcher

    assert hasattr(GitHubFetcher, "update_issue_body")
    sig = inspect.signature(GitHubFetcher.update_issue_body)
    assert "owner" in sig.parameters
    assert "repo" in sig.parameters
    assert "issue_number" in sig.parameters
    assert "new_body" in sig.parameters


def test_github_fetcher_no_add_comment() -> None:
    from autoskillit.core.types._type_protocols_github import GitHubFetcher

    assert not hasattr(GitHubFetcher, "add_comment"), (
        "add_comment must be removed after all call sites are migrated"
    )


def test_skill_lister_protocol_defined() -> None:
    """REQ-PROTO-007: SkillLister Protocol must live in
    core/_type_protocols_workspace.py and define a `list_all() -> list[Any]`
    method, so L2 recipe code can type-annotate against L0 instead of
    binding to the L1 workspace concrete class."""
    from autoskillit.core.types._type_protocols_workspace import SkillLister

    assert hasattr(SkillLister, "list_all")
    sig = inspect.signature(SkillLister.list_all)
    assert "self" in sig.parameters


def test_skill_resolver_satisfies_skill_lister() -> None:
    from autoskillit.core.types._type_protocols_workspace import SkillLister
    from autoskillit.workspace.skills import DefaultSkillResolver

    instance: SkillLister = DefaultSkillResolver()
    assert callable(instance.list_all)


def test_recipe_repository_load_and_validate_project_dir_annotation() -> None:
    import typing
    from pathlib import Path

    from autoskillit.core.types._type_protocols_recipe import RecipeRepository

    hints = typing.get_type_hints(RecipeRepository.load_and_validate)
    ann = hints["project_dir"]
    args = typing.get_args(ann)
    assert Path in args, f"Expected Path in annotation args, got: {ann}"
    assert str in args, f"Expected str in annotation args, got: {ann}"
