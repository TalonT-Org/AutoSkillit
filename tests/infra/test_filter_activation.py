"""Infrastructure tests: verify test path filtering is activated in project config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).parent.parent.parent


def test_project_config_has_operational_test_gate_tuple():
    """The repository's full test gate has its required command and budget."""
    from autoskillit.core.io import load_yaml

    cfg = load_yaml(REPO_ROOT / ".autoskillit/config.yaml")
    test_check = cfg["test_check"]
    assert test_check["command"] == ["task", "test-all"]
    assert test_check["timeout"] == 900
    assert test_check["filter_mode"] == "conservative"
    assert test_check["base_ref"] == "develop"


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


def test_quota_check_not_in_infra() -> None:
    """test_quota_check.py was moved to hooks/; assert no stale infra/ reference remains."""
    assert not (REPO_ROOT / "tests/infra/test_quota_check.py").is_file()
    assert (REPO_ROOT / "tests/hooks/test_quota_check.py").is_file()


def test_source_map_quota_check_path_is_hooks() -> None:
    """Committed source map must reference hooks/, not infra/, for test_quota_check.py."""
    map_path = REPO_ROOT / ".autoskillit/test-source-map.json"
    assert map_path.is_file(), "committed test-source-map.json must exist"
    raw = map_path.read_text()
    assert "tests/infra/test_quota_check.py" not in raw, (
        "stale tests/infra/test_quota_check.py found in .autoskillit/test-source-map.json"
    )
    assert "tests/hooks/test_quota_check.py" in raw, (
        "tests/hooks/test_quota_check.py not found in .autoskillit/test-source-map.json"
    )


def test_backend_gating_source_map_uses_canonical_test_module() -> None:
    """Guard against partial reverts restoring the retired test or its source-map links."""
    assert not (REPO_ROOT / "tests/test_backend_gating_root.py").is_file()
    assert (REPO_ROOT / "tests/test_llm_triage.py").is_file()

    source_map = json.loads((REPO_ROOT / ".autoskillit/test-source-map.json").read_text())["map"]
    mapped_tests = {
        test_path for source_tests in source_map.values() for test_path in source_tests
    }
    assert "tests/test_backend_gating_root.py" not in mapped_tests
    assert "tests/test_llm_triage.py" in source_map["src/autoskillit/_llm_triage.py"]


def test_build_test_scope_returns_full_run_reason_for_unmapped():
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope

    changed = {"scripts/random_script.py"}
    result = build_test_scope(changed_files=changed, mode=FilterMode.CONSERVATIVE)
    assert result is FullRunReason.UNMAPPED_FILE


def test_build_test_scope_routes_known_script_via_manifest():
    from tests._test_filter import FilterMode, FullRunReason, build_test_scope, load_manifest

    manifest = load_manifest(REPO_ROOT)
    changed = {"scripts/benchmark-testmon.py"}
    result = build_test_scope(
        changed_files=changed, mode=FilterMode.CONSERVATIVE, manifest=manifest
    )
    assert result is not FullRunReason.UNMAPPED_FILE
    assert isinstance(result, set)
    assert any(p.name == "infra" for p in result)
