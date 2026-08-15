import subprocess
from pathlib import Path

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
    """Direct and rerun paths each converge on one non-looping finalization."""
    merge = recipe.steps["merge_escalations"]
    fallthrough_routes = [cond.route for cond in merge.on_result.conditions if cond.when is None]
    assert "finalize_bundle" in fallthrough_routes, "the no-rerun path must reach finalize_bundle"
    assert recipe.steps["re_push_research"].on_success == "finalize_bundle", (
        "the rerun path must reach finalize_bundle after publishing rerun results"
    )
    assert recipe.steps["finalize_bundle"].on_success == "push_finalized_bundle", (
        "finalize_bundle must not loop back through re_push_research"
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
    """The no-rerun path compresses before publishing the finalized commit."""
    merge = recipe.steps["merge_escalations"]
    # The fallthrough route (last on_result entry without a when-condition) must
    # be finalize_bundle, not re_push_research.
    fallthrough_routes = [cond.route for cond in merge.on_result.conditions if cond.when is None]
    assert fallthrough_routes == ["finalize_bundle"], (
        "merge_escalations fallthrough must route to finalize_bundle "
        "so the compression commit is created before the push"
    )


def test_finalize_bundle_on_success_routes_to_push_finalized_bundle(recipe):
    """finalize_bundle must push its compression commit before rendering."""
    step = recipe.steps["finalize_bundle"]
    assert step.on_success == "push_finalized_bundle", (
        "finalize_bundle.on_success must push_finalized_bundle so the compression "
        "commit is pushed exactly once"
    )


def test_re_push_research_on_success_routes_to_finalize_bundle(recipe):
    """The revalidation push advances to the single compression point."""
    step = recipe.steps["re_push_research"]
    assert step.on_success == "finalize_bundle", (
        "re_push_research.on_success must be finalize_bundle"
    )


def test_push_finalized_bundle_routes_to_render_or_archival(recipe):
    """The compression commit is published before render; push failure still archives."""
    step = recipe.steps["push_finalized_bundle"]
    assert step.on_success == "finalize_bundle_render"
    assert step.on_failure == "begin_archival"


@pytest.mark.medium
def test_finalize_bundle_script_manifest_idempotent(tmp_path):
    """finalize_bundle.sh must not append ## Archive Manifest more than once.

    The idempotency guard checks for existing '## Archive Manifest' before appending,
    so re-running the script produces the manifest section exactly once.
    """
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()

    (research_dir / "report.md").write_text("# Research Report\n\nSome content.\n")
    (research_dir / "data.csv").write_text("col1,col2\nval1,val2\n")

    script_path = (
        Path(__file__).parent.parent.parent
        / "src/autoskillit/recipes/scripts"
        / "finalize_bundle.sh"
    )

    def run_finalize():
        result = subprocess.run(
            [str(script_path), "local", str(research_dir), str(worktree_path)],
            capture_output=True,
            text=True,
        )
        return result

    r1 = run_finalize()
    assert r1.returncode == 0, f"first run failed: {r1.stderr}"

    r2 = run_finalize()
    assert r2.returncode == 0, f"second run failed: {r2.stderr}"

    report_content = (research_dir / "report.md").read_text()
    assert report_content.count("## Archive Manifest") == 1, (
        "## Archive Manifest must appear exactly once after two script runs; "
        f"found {report_content.count('## Archive Manifest')}"
    )
