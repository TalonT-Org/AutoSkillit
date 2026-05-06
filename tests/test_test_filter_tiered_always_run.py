"""Tests for tiered conditional always-run logic (REQ-TIER-001 through REQ-TIER-005)."""

from pathlib import Path

from tests._test_filter import (
    _HOOKS_UNCONDITIONAL_FILES,
    _INFRA_UNCONDITIONAL_FILES,
    FilterMode,
    build_test_scope,
)


def _make_tests_root(tmp_path: Path, dirs: list[str]) -> Path:
    tests_root = tmp_path / "tests"
    for d in dirs:
        (tests_root / d).mkdir(parents=True, exist_ok=True)
    return tests_root


ALL_DIRS = [
    "core",
    "config",
    "execution",
    "pipeline",
    "workspace",
    "recipe",
    "migration",
    "server",
    "cli",
    "hooks",
    "skills",
    "arch",
    "contracts",
    "infra",
    "docs",
]


class TestTieredAlwaysRun:
    def test_pure_cli_change_skips_infra_dir_and_docs_dir(self, tmp_path: Path) -> None:
        """REQ-TIER-001/002/003: cli change → arch+contracts present; infra/docs NOT as dirs;
        6 infra + 3 hooks unconditional files in result; test_doc_counts.py in result."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/cli/app.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result if (tests_root / p.name).is_dir()}
        assert "arch" in dir_names
        assert "contracts" in dir_names
        assert "infra" not in dir_names, "full infra dir should not appear for pure cli change"
        assert "docs" not in dir_names, "docs dir should not appear for pure cli change"
        result_names = {p.name for p in result}
        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"
        for fname in _HOOKS_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional hooks file {fname!r} missing"
        # Verify parent directories
        for p in result:
            if p.name in _INFRA_UNCONDITIONAL_FILES:
                assert p.parent.name == "infra", f"{p.name} expected under infra/"
            if p.name in _HOOKS_UNCONDITIONAL_FILES:
                assert p.parent.name == "hooks", f"{p.name} expected under hooks/"
        assert "test_doc_counts.py" in result_names

    def test_docs_change_includes_docs_dir(self, tmp_path: Path) -> None:
        """REQ-TIER-002: docs/ change → docs directory included."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        result = build_test_scope(
            changed_files={"docs/developer/SETUP.md"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            manifest={"docs/**/*.md": ["docs"]},
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "docs" in dir_names

    def test_hooks_change_includes_full_infra_dir(self, tmp_path: Path) -> None:
        """REQ-TIER-003: hooks source change → full infra directory included."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/hooks/guards/quota_guard.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "infra" in dir_names

    def test_github_workflow_change_includes_full_infra_dir(self, tmp_path: Path) -> None:
        """REQ-TIER-003: .github/ change → full infra directory included."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        result2 = build_test_scope(
            changed_files={".github/workflows/ci.yml"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            manifest={".github/workflows/*.yml": ["infra"]},
        )
        assert result2 is not None
        dir_names = {p.name for p in result2}
        assert "infra" in dir_names

    def test_empty_changed_files_uses_full_always_run(self, tmp_path: Path) -> None:
        """REQ-TIER-004: empty changed_files → fail-open → full always-run set as dirs."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        result = build_test_scope(
            changed_files=set(),
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for d in ["arch", "contracts", "infra", "docs"]:
            assert d in dir_names, f"fail-open: {d} must be present for empty changeset"

    def test_unconditional_files_constants_have_correct_counts(self) -> None:
        """_INFRA_UNCONDITIONAL_FILES has 6 entries; _HOOKS_UNCONDITIONAL_FILES has 3 entries."""
        assert len(_INFRA_UNCONDITIONAL_FILES) == 6
        assert len(_HOOKS_UNCONDITIONAL_FILES) == 3

    def test_infra_unconditional_files_resolve_under_infra_dir(self, tmp_path: Path) -> None:
        """Infra unconditional files must resolve to tests/infra/."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        for fname in _INFRA_UNCONDITIONAL_FILES:
            (tests_root / "infra" / fname).touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/cli/app.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        infra_results = [p for p in result if p.name in _INFRA_UNCONDITIONAL_FILES]
        assert len(infra_results) == len(_INFRA_UNCONDITIONAL_FILES)
        for p in infra_results:
            assert p.parent.name == "infra", f"{p.name} expected under infra/, got {p.parent.name}"

    def test_hook_unconditional_files_resolve_under_hooks_dir(self, tmp_path: Path) -> None:
        """Hook unconditional files must resolve to tests/hooks/, not tests/infra/."""
        tests_root = _make_tests_root(tmp_path, ALL_DIRS)
        # Create placeholder files so path assertions are meaningful
        for fname in _HOOKS_UNCONDITIONAL_FILES:
            (tests_root / "hooks" / fname).touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/cli/app.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        hooks_results = [p for p in result if p.name in _HOOKS_UNCONDITIONAL_FILES]
        assert len(hooks_results) == len(_HOOKS_UNCONDITIONAL_FILES)
        for p in hooks_results:
            assert p.parent.name == "hooks", f"{p.name} expected under hooks/, got {p.parent.name}"

    def test_aggressive_mode_unaffected_by_tiered_logic(self, tmp_path: Path) -> None:
        """Tiered logic only applies to CONSERVATIVE mode; aggressive uses its own always-run."""
        tests_root = _make_tests_root(tmp_path, ["core", "arch", "contracts"])
        result = build_test_scope(
            changed_files={"src/autoskillit/cli/app.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "arch" in dir_names
        assert "contracts" in dir_names
        assert "infra" not in dir_names  # not in ALWAYS_RUN_AGGRESSIVE
