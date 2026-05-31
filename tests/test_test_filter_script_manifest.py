"""Tests for scripts/*.py manifest routing in build_test_scope."""

from __future__ import annotations

from pathlib import Path

from tests._test_filter import FilterMode, FullRunReason, build_test_scope


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
