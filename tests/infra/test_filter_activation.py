"""Infrastructure tests: verify test path filtering is activated in project config."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_project_config_has_filter_mode_conservative():
    """AC1: .autoskillit/config.yaml must set filter_mode to conservative."""
    from autoskillit.core.io import load_yaml

    cfg = load_yaml(REPO_ROOT / ".autoskillit/config.yaml")
    assert cfg["test_check"]["filter_mode"] == "conservative"


def test_project_config_has_base_ref():
    """AC1: .autoskillit/config.yaml must set base_ref to develop."""
    from autoskillit.core.io import load_yaml

    cfg = load_yaml(REPO_ROOT / ".autoskillit/config.yaml")
    assert cfg["test_check"]["base_ref"] == "develop"


def test_hook_registry_tests_in_infra():
    """AC4: test_hook_registry.py must live in tests/hooks/."""
    assert (REPO_ROOT / "tests/hooks/test_hook_registry.py").is_file()
    assert not (REPO_ROOT / "tests/test_hook_registry.py").is_file()


def test_phase2_skills_in_skills():
    """AC4: test_phase2_skills.py must live in tests/skills/."""
    assert (REPO_ROOT / "tests/skills/test_phase2_skills.py").is_file()
    assert not (REPO_ROOT / "tests/test_phase2_skills.py").is_file()


def test_skill_preambles_in_skills():
    """AC4: test_skill_preambles.py must live in tests/skills/."""
    assert (REPO_ROOT / "tests/skills/test_skill_preambles.py").is_file()
    assert not (REPO_ROOT / "tests/test_skill_preambles.py").is_file()


def test_ci_filter_codepath_produces_scope():
    """CI codepath: conservative mode + known source file -> non-None scope."""
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope, load_manifest

    manifest = load_manifest(REPO_ROOT)
    scope = build_test_scope(
        changed_files={"src/autoskillit/core/paths.py"},
        mode=FilterMode.CONSERVATIVE,
        manifest=manifest,
        tests_root=REPO_ROOT / "tests",
        cwd=REPO_ROOT,
        base_ref="develop",
    )
    assert not isinstance(scope, FullRunReason), (
        "build_test_scope must return a set scope for conservative mode "
        "with a known source file — FullRunReason means silent fallback to full run"
    )
    assert len(scope) > 0, "Scope must contain at least one test path"


def test_build_test_scope_returns_full_run_reason_for_large_changeset():
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope

    changed = {f"src/autoskillit/fake_{i}.py" for i in range(35)}
    result = build_test_scope(changed_files=changed, mode=FilterMode.CONSERVATIVE)
    assert result is FullRunReason.LARGE_CHANGESET


def test_build_test_scope_returns_full_run_reason_for_git_unavailable():
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope

    result = build_test_scope(changed_files=None, mode=FilterMode.CONSERVATIVE)
    assert result is FullRunReason.GIT_UNAVAILABLE


def test_build_test_scope_returns_full_run_reason_for_bucket_a():
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope

    changed = {"pyproject.toml"}
    result = build_test_scope(changed_files=changed, mode=FilterMode.CONSERVATIVE)
    assert result is FullRunReason.BUCKET_A


def test_build_test_scope_returns_full_run_reason_for_unmapped():
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope

    changed = {"scripts/random_script.py"}
    result = build_test_scope(changed_files=changed, mode=FilterMode.CONSERVATIVE)
    assert result is FullRunReason.UNMAPPED_FILE
