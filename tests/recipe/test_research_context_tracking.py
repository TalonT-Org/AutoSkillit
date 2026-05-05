from __future__ import annotations

import re

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules, validate_recipe

RESEARCH_RECIPE_PATH = builtin_recipes_dir() / "research.yaml"
SCRIPTS_DIR = pkg_root().parent.parent / "scripts" / "recipe"

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_FIND_HEURISTIC_RE = re.compile(r"\bfind\b.+\|\s*sort\b.+\|\s*(tail|head)\b")


def _has_find_heuristic(text: str) -> bool:
    return bool(_FIND_HEURISTIC_RE.search(text))


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(RESEARCH_RECIPE_PATH)


def test_create_worktree_captures_research_dir(recipe):
    step = recipe.steps["create_worktree"]
    assert "research_dir" in step.capture, (
        "create_worktree must capture research_dir for downstream steps"
    )


def test_create_worktree_script_emits_research_dir():
    script_path = SCRIPTS_DIR / "create_worktree.sh"
    assert script_path.exists(), f"create_worktree.sh not found at {script_path}"
    script = script_path.read_text()
    assert 'echo "research_dir=' in script, (
        "create_worktree.sh must emit research_dir= to stdout for capture"
    )


def test_create_worktree_script_has_git_init_guard():
    """create_worktree.sh must auto-init a git repo when .git is absent."""
    script_path = SCRIPTS_DIR / "create_worktree.sh"
    script = script_path.read_text()
    assert '[ ! -d "$SOURCE_DIR/.git" ]' in script or "! -d" in script, (
        "create_worktree.sh must check for .git directory absence"
    )
    assert "git init" in script, "create_worktree.sh must run git init when .git is missing"
    assert "--allow-empty" in script, (
        "create_worktree.sh must create an empty seed commit for git worktree add"
    )


def test_create_worktree_git_init_guard_precedes_worktree_add():
    """The git-init guard must appear BEFORE git worktree add."""
    script_path = SCRIPTS_DIR / "create_worktree.sh"
    lines = script_path.read_text().splitlines()
    init_line = next(i for i, ln in enumerate(lines) if "git init" in ln)
    worktree_line = next(i for i, ln in enumerate(lines) if "worktree add" in ln)
    assert init_line < worktree_line, "git init guard must be PREPENDED before git worktree add"


def test_finalize_bundle_uses_context_research_dir(recipe):
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert "context.research_dir" in cmd, (
        "finalize_bundle must source research_dir from context, not re-discover it"
    )


def test_finalize_bundle_no_find_heuristic(recipe):
    step = recipe.steps["finalize_bundle"]
    cmd = step.with_args.get("cmd", "")
    assert not _has_find_heuristic(cmd), (
        "finalize_bundle cmd uses find|sort|tail heuristic — must use context.research_dir instead"
    )
    script_path = SCRIPTS_DIR / "finalize_bundle.sh"
    assert script_path.exists(), f"finalize_bundle.sh not found at {script_path}"
    script = script_path.read_text()
    assert not _has_find_heuristic(script), (
        "finalize_bundle.sh uses find|sort|tail heuristic — "
        "must use positional arg from context.research_dir instead"
    )


def test_create_artifact_branch_scopes_via_research_dir(recipe):
    step = recipe.steps["create_artifact_branch"]
    cmd = step.with_args.get("cmd", "")
    assert "context.research_dir" in cmd, (
        "create_artifact_branch must pass context.research_dir to script"
    )
    script_path = SCRIPTS_DIR / "create_artifact_branch.sh"
    assert script_path.exists(), f"create_artifact_branch.sh not found at {script_path}"
    script = script_path.read_text()
    assert "basename" in script, "create_artifact_branch.sh must use basename to scope checkout"


def test_finalize_bundle_has_post_compression_guard():
    script_path = SCRIPTS_DIR / "finalize_bundle.sh"
    assert script_path.exists(), f"finalize_bundle.sh not found at {script_path}"
    script = script_path.read_text()
    assert re.search(
        r"artifacts\.tar\.gz[^\n]*\|\|[^\n]*exit\s+1|artifacts\.tar\.gz.*\\\n.*exit\s+1", script
    ), "finalize_bundle.sh must conditionally exit 1 when artifacts.tar.gz is absent"


def test_research_recipe_passes_semantic_rules(recipe):
    errors = validate_recipe(recipe)
    assert not errors, f"Structural validation errors: {errors}"
    findings = run_semantic_rules(recipe)
    error_findings = [f for f in findings if f.severity.value == "error"]
    assert not error_findings, (
        f"Semantic rule errors: {[f'{f.rule}: {f.message}' for f in error_findings]}"
    )
