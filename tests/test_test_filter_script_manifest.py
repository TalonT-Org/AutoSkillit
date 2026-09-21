"""Tests for scripts/*.py manifest routing in build_test_scope."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit._test_filter import apply_manifest as apply_production_manifest
from autoskillit._test_filter import load_manifest as load_production_manifest
from tests._git_inventory import git_ls_files
from tests._test_filter import FilterMode, FullRunReason, build_test_scope

pytestmark = [pytest.mark.medium]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_MANIFEST = _REPO_ROOT / ".autoskillit" / "test-filter-manifest.yaml"


def test_every_tracked_python_script_has_the_infra_manifest_route() -> None:
    manifest = load_production_manifest(_PRODUCTION_MANIFEST)

    for script in git_ls_files(_REPO_ROOT):
        if script.startswith("scripts/") and script.endswith(".py"):
            result = apply_production_manifest([script], manifest)
            assert result is not None, f"{script} has no production manifest route"
            assert "infra/" in result, f"{script} is missing the scripts/*.py infrastructure route"


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("scripts/check_file_lengths.py", {"arch/", "infra/"}),
        ("scripts/check_single_enforcement_point.py", {"contracts/", "infra/"}),
        ("scripts/pytest_tmp_lifecycle.py", {"arch/", "infra/"}),
        ("scripts/sync_hook_scope_table.py", {"hooks/", "infra/"}),
    ],
)
def test_new_script_routes_are_exact_additive_unions(script: str, expected: set[str]) -> None:
    manifest = load_production_manifest(_PRODUCTION_MANIFEST)
    assert apply_production_manifest([script], manifest) == expected


def test_scope_script_py_manifest_match(tmp_path: Path) -> None:
    """scripts/*.py files with a manifest entry route via manifest, not UNMAPPED_FILE."""
    tests_root = tmp_path / "tests"
    for d in ["infra", "docs", "arch"]:
        (tests_root / d).mkdir(parents=True, exist_ok=True)

    manifest = {"scripts/check_sub_claude_md.py": ["docs"]}
    result = build_test_scope(
        changed_files={"scripts/check_sub_claude_md.py"},
        mode=FilterMode.CONSERVATIVE,
        manifest=manifest,
        tests_root=tests_root,
    )
    assert isinstance(result, set)
    assert {p.name for p in result} >= {"docs"}


def test_scope_script_py_no_manifest_match(tmp_path: Path) -> None:
    """scripts/*.py without manifest entry still returns UNMAPPED_FILE."""
    tests_root = tmp_path / "tests"
    (tests_root / "infra").mkdir(parents=True, exist_ok=True)

    manifest = {"scripts/other_script.py": ["infra"]}
    result = build_test_scope(
        changed_files={"scripts/unknown_script.py"},
        mode=FilterMode.CONSERVATIVE,
        manifest=manifest,
        tests_root=tests_root,
    )
    assert result is FullRunReason.UNMAPPED_FILE


def test_scope_script_py_none_manifest(tmp_path: Path) -> None:
    """scripts/*.py with manifest=None returns UNMAPPED_FILE (fail-open)."""
    tests_root = tmp_path / "tests"
    (tests_root / "infra").mkdir(parents=True, exist_ok=True)

    result = build_test_scope(
        changed_files={"scripts/benchmark-testmon.py"},
        mode=FilterMode.CONSERVATIVE,
        manifest=None,
        tests_root=tests_root,
    )
    assert result is FullRunReason.UNMAPPED_FILE


def test_scope_mixed_script_and_src_py(tmp_path: Path) -> None:
    """scripts/*.py via manifest + src/*.py via cascade both contribute test_dirs."""
    tests_root = tmp_path / "tests"
    for d in ["infra", "core"]:
        (tests_root / d).mkdir(parents=True, exist_ok=True)

    manifest = {"scripts/sync_versions.py": ["infra"]}
    result = build_test_scope(
        changed_files={"scripts/sync_versions.py", "src/autoskillit/core/paths.py"},
        mode=FilterMode.CONSERVATIVE,
        manifest=manifest,
        tests_root=tests_root,
    )
    assert isinstance(result, set)
    names = {p.name for p in result}
    assert "infra" in names
    assert "core" in names
