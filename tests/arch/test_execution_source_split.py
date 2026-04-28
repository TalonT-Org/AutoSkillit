"""Arch guards for the headless.py source split (P8-F1 audit fix)."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC_EXECUTION = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "execution"

NEW_HEADLESS_MODULES = [
    "_headless_recovery.py",
    "_headless_path_tokens.py",
    "_headless_result.py",
]
HEADLESS_SIZE_BUDGETS = {
    "headless.py": 750,
    "_headless_recovery.py": 320,
    "_headless_path_tokens.py": 175,
    "_headless_result.py": 580,
}


def test_new_headless_modules_exist():
    for name in NEW_HEADLESS_MODULES:
        assert (SRC_EXECUTION / name).exists(), f"Missing: {name}"


def test_headless_facade_under_budget():
    for name, limit in HEADLESS_SIZE_BUDGETS.items():
        lines = len((SRC_EXECUTION / name).read_text().splitlines())
        assert lines <= limit, f"{name}: {lines} lines exceeds budget of {limit}"


def test_headless_facade_does_not_define_build_skill_result():
    src = (SRC_EXECUTION / "headless.py").read_text()
    assert "def _build_skill_result" not in src, (
        "_build_skill_result must live in _headless_result.py, not headless.py"
    )


def test_headless_facade_does_not_define_recovery_functions():
    src = (SRC_EXECUTION / "headless.py").read_text()
    for fn in (
        "_recover_from_separate_marker",
        "_recover_block_from_assistant_messages",
        "_synthesize_from_write_artifacts",
        "_extract_missing_token_hints",
        "_attempt_contract_nudge",
    ):
        assert f"def {fn}" not in src, f"{fn} must live in _headless_recovery.py"


def test_headless_facade_does_not_define_path_token_functions():
    src = (SRC_EXECUTION / "headless.py").read_text()
    for fn in (
        "_build_path_token_set",
        "_extract_output_paths",
        "_validate_output_paths",
        "_extract_worktree_path",
    ):
        assert f"def {fn}" not in src, f"{fn} must live in _headless_path_tokens.py"
