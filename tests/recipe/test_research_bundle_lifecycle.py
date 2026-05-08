import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_stage_bundle_is_idempotent(recipe):
    """stage_bundle invokes an external script; no inline compression or commit."""
    step = recipe.steps["stage_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "stage_bundle.sh" in cmd, "stage_bundle must delegate to scripts/recipe/stage_bundle.sh"
    assert "tar czf" not in cmd and "tar -czf" not in cmd, (
        "stage_bundle must NOT compress — second run must be a no-op"
    )
    assert "git commit" not in cmd, "stage_bundle must NOT commit"


def test_stage_bundle_does_not_compress(recipe):
    """stage_bundle must not create artifacts.tar.gz, rename report.md, or commit."""
    step = recipe.steps["stage_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "artifacts.tar.gz" not in cmd, "stage_bundle must not reference artifacts.tar.gz"
    assert "README.md" not in cmd, "stage_bundle must not rename report.md to README.md"
    assert "git commit" not in cmd, "stage_bundle must not commit"


def test_finalize_bundle_pr_mode(recipe):
    """finalize_bundle must delegate to external script with output_mode, research_dir."""
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "finalize_bundle.sh" in cmd, (
        "finalize_bundle must delegate to scripts/recipe/finalize_bundle.sh"
    )
    assert "inputs.output_mode" in cmd, (
        "finalize_bundle script must receive output_mode as first argument"
    )
    assert "context.research_dir" in cmd, (
        "finalize_bundle script must receive research_dir as argument"
    )
    assert "context.worktree_path" in cmd, (
        "finalize_bundle script must receive worktree_path as argument"
    )


def test_finalize_bundle_runs_exactly_once_after_rerun(recipe):
    """finalize_bundle entry point is merge_escalations (not re_push_research)."""
    # merge_escalations is the step that routes to finalize_bundle
    merge = recipe.steps["merge_escalations"]
    fallthrough_routes = [cond.route for cond in merge.on_result.conditions if cond.when is None]
    assert "finalize_bundle" in fallthrough_routes, (
        "merge_escalations fallthrough must reach finalize_bundle exactly once"
    )
    # test and retest do NOT route to finalize_bundle (they route to push_branch)
    assert recipe.steps["test"].on_success != "finalize_bundle"
    assert recipe.steps["retest"].on_success != "finalize_bundle"
    # stage_bundle does not compress — guarantee that the early staging can't trigger finalize
    stage_cmd = recipe.steps["stage_bundle"].with_args.get("cmd", "")
    assert "tar czf" not in stage_cmd and "tar -czf" not in stage_cmd, (
        "stage_bundle must not compress — only finalize_bundle may produce artifacts.tar.gz"
    )


def test_compression_commit_precedes_push(recipe):
    """merge_escalations must route to finalize_bundle, not re_push_research."""
    merge = recipe.steps["merge_escalations"]
    # The fallthrough route (last on_result entry without a when-condition) must
    # be finalize_bundle, not re_push_research.
    fallthrough_routes = [cond.route for cond in merge.on_result.conditions if cond.when is None]
    assert fallthrough_routes == ["finalize_bundle"], (
        "merge_escalations fallthrough must route to finalize_bundle "
        "so the compression commit is created before the push"
    )


def test_finalize_bundle_on_success_routes_to_re_push_research(recipe):
    """finalize_bundle must push after committing — on_success must be re_push_research."""
    step = recipe.steps["finalize_bundle"]
    assert step.on_success == "re_push_research", (
        "finalize_bundle.on_success must be re_push_research so the compression "
        "commit is included in the push"
    )


def test_re_push_research_on_success_routes_to_finalize_bundle_render(recipe):
    """re_push_research must advance to HTML rendering, not loop back to finalize_bundle."""
    step = recipe.steps["re_push_research"]
    assert step.on_success == "finalize_bundle_render", (
        "re_push_research.on_success must be finalize_bundle_render"
    )
