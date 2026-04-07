import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules, validate_recipe

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


def test_create_worktree_emits_research_dir(recipe):
    """create_worktree cmd must emit echo "research_dir=..." to stdout."""
    step = recipe.steps["create_worktree"]
    cmd = step.with_args.get("cmd", "")
    assert 'echo "research_dir=' in cmd, (
        "create_worktree must emit research_dir= to stdout so it can be captured"
    )


def test_create_worktree_captures_research_dir(recipe):
    """create_worktree capture block must include research_dir key."""
    step = recipe.steps["create_worktree"]
    assert "research_dir" in step.capture, (
        "create_worktree must capture research_dir for downstream steps"
    )


def test_commit_research_artifacts_uses_context_research_dir(recipe):
    """commit_research_artifacts must reference context.research_dir, not find."""
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    assert "context.research_dir" in cmd, (
        "commit_research_artifacts must source RESEARCH_DIR from context"
    )


def test_commit_research_artifacts_no_find_heuristic(recipe):
    """commit_research_artifacts must not use find|sort|tail -1 to pick the directory."""
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    has_heuristic = "find" in cmd and "sort" in cmd and "tail" in cmd
    assert not has_heuristic, (
        "commit_research_artifacts uses find|sort|tail heuristic — "
        "replace with ${{ context.research_dir }}"
    )


def test_create_artifact_branch_scopes_checkout(recipe):
    """create_artifact_branch must scope git checkout to the specific experiment subdir."""
    step = recipe.steps["create_artifact_branch"]
    cmd = step.with_args.get("cmd", "")
    assert "basename" in cmd and "context.research_dir" in cmd, (
        "create_artifact_branch must scope checkout via basename of context.research_dir"
    )


def test_commit_research_artifacts_has_post_compression_guard(recipe):
    """commit_research_artifacts must guard that artifacts.tar.gz exists before git commit."""
    step = recipe.steps["commit_research_artifacts"]
    cmd = step.with_args.get("cmd", "")
    assert "artifacts.tar.gz" in cmd and "exit 1" in cmd, (
        "commit_research_artifacts must fail loudly if artifacts.tar.gz is absent"
    )


def test_research_recipe_validates_with_new_rules(recipe):
    """research.yaml must pass validate_recipe AND all semantic rules including run-cmd rules."""
    errors = validate_recipe(recipe)
    assert not errors, errors
    findings = run_semantic_rules(recipe)
    errors_only = [f for f in findings if f.severity.value == "error"]
    assert not errors_only, [f.message for f in errors_only]
