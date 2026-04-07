import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import validate_recipe

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_research_recipe_validates(recipe):
    errors = validate_recipe(recipe)
    assert not errors, f"Validation errors: {errors}"


def test_commit_research_artifacts_creates_tarball(recipe):
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    assert "tar czf" in cmd or "tar -czf" in cmd


def test_commit_research_artifacts_renames_report_to_readme(recipe):
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    assert "README.md" in cmd
    assert "report.md" in cmd


def test_commit_research_artifacts_removes_uncompressed(recipe):
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    assert "rm " in cmd or "rm -rf" in cmd


def test_open_artifact_pr_documents_tarball(recipe):
    step = recipe.steps["open_artifact_pr"]
    cmd = step.with_args.get("cmd", "")
    assert "tar xzf" in cmd or "tar -xzf" in cmd
    assert "artifacts.tar.gz" in cmd


def test_tarball_preserves_directory_structure(recipe):
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    assert "-C" in cmd or "--directory" in cmd


def test_create_worktree_unchanged(recipe):
    step = recipe.steps["create_worktree"]
    cmd = step.with_args.get("cmd", "")
    assert "tar " not in cmd
    assert "artifacts.tar.gz" not in cmd
    assert "README.md" not in cmd


def test_commit_research_artifacts_routing_preserved(recipe):
    step = recipe.steps["commit_research_artifacts"]
    assert step.on_success == "push_branch"
    assert step.on_failure == "push_branch"
