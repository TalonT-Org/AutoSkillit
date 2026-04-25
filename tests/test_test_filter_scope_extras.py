"""Tests for file-level scope support in build_test_scope (AC3, AC4)."""

from __future__ import annotations

from pathlib import Path

from tests._test_filter import FilterMode, build_test_scope


def test_build_test_scope_includes_file_entries_from_manifest(tmp_path: Path) -> None:
    """AC3: Manifest entries pointing to files (not dirs) must be included in the scope."""
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    target_file = tests_root / "test_version.py"
    target_file.write_text("def test_v(): pass\n")
    sub_dir = tests_root / "contracts"
    sub_dir.mkdir()

    manifest = {"some/config.json": ["contracts/", "test_version.py"]}
    scope = build_test_scope(
        {"some/config.json"},
        FilterMode.CONSERVATIVE,
        manifest=manifest,
        tests_root=str(tests_root),
    )
    assert scope is not None
    assert target_file in scope


def test_build_test_scope_skills_extended_manifest(tmp_path: Path) -> None:
    """AC4: skills_extended/*/SKILL.md changes must include skills_extended/ in scope."""
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "skills_extended").mkdir()

    manifest = {
        "src/autoskillit/skills_extended/foo/SKILL.md": [
            "skills/",
            "contracts/",
            "recipe/",
            "skills_extended/",
        ]
    }
    scope = build_test_scope(
        {"src/autoskillit/skills_extended/foo/SKILL.md"},
        FilterMode.CONSERVATIVE,
        manifest=manifest,
        tests_root=str(tests_root),
    )
    assert scope is not None
    assert tests_root / "skills_extended" in scope
