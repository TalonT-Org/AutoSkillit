import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe")]

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_stage_bundle_is_idempotent(recipe):
    """stage_bundle cmd uses cp (idempotent); no mv/rename, no compression."""
    step = recipe.steps["stage_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "cp " in cmd, "stage_bundle must use cp for idempotent file copying"
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
    """finalize_bundle must rename report.md→README.md, compress, append manifest, commit."""
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    # rename
    assert "README.md" in cmd and "report.md" in cmd, (
        "finalize_bundle must rename report.md to README.md"
    )
    # compression
    assert "tar czf" in cmd or "tar -czf" in cmd, "finalize_bundle must create tarball"
    assert "-C" in cmd or "--directory" in cmd, "finalize_bundle must use -C for relative paths"
    # manifest
    assert "tar tzf" in cmd, "finalize_bundle must generate archive manifest"
    assert "Archive Manifest" in cmd, "finalize_bundle manifest section header required"
    assert ">> " in cmd and "README.md" in cmd, "manifest must be appended (>>) to README.md"
    # post-compression guard
    assert "artifacts.tar.gz" in cmd and "exit 1" in cmd, (
        "finalize_bundle must guard that artifacts.tar.gz exists or exit 1"
    )
    # commit
    assert "git commit" in cmd, "finalize_bundle must commit the result"
    # dynamic tar inputs
    assert "ls -1" in cmd and "grep -vE" in cmd, (
        "finalize_bundle must use ls -1 | grep -vE for dynamic TAR_ITEMS"
    )
    # cleanup loop
    assert "for item in" in cmd and "rm -rf" in cmd, (
        "finalize_bundle must rm -rf each archived item"
    )
    # rename before archive (rename_pos < tar_pos)
    rename_pos = cmd.find("report.md")
    tar_pos = cmd.find("tar czf")
    assert rename_pos < tar_pos, (
        "report.md rename must appear before tar czf so README.md is excluded"
    )


def test_finalize_bundle_runs_exactly_once_after_rerun(recipe):
    """finalize_bundle is only reachable via re_push_research.on_success."""
    # only entry point is re_push_research.on_success
    re_push = recipe.steps["re_push_research"]
    assert re_push.on_success == "finalize_bundle", (
        "re_push_research.on_success must be finalize_bundle"
    )
    # test and retest do NOT route to finalize_bundle (they route to push_branch)
    assert recipe.steps["test"].on_success != "finalize_bundle"
    assert recipe.steps["retest"].on_success != "finalize_bundle"
    # stage_bundle does not compress — guarantee that the early staging can't trigger finalize
    stage_cmd = recipe.steps["stage_bundle"].with_args.get("cmd", "")
    assert "tar czf" not in stage_cmd and "tar -czf" not in stage_cmd, (
        "stage_bundle must not compress — only finalize_bundle may produce artifacts.tar.gz"
    )
