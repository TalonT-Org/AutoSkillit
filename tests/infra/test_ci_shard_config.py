# tests/infra/test_ci_shard_config.py
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "tests.yml"

KNOWN_GENERAL_SHARD_DIRS = {
    "arch",
    "assets",
    "cli",
    "config",
    "docs",
    "fixtures",
    "fleet",
    "hooks",
    "infra",
    "recipe",
    "report",
    "server",
    "skills",
    "skills_extended",
    "workspace",
}


def _parse_shard_a_dirs_from_workflow() -> list[str]:
    if not WORKFLOW_PATH.exists():
        pytest.skip(
            f"Workflow file not found: {WORKFLOW_PATH}",
            allow_module_level=True,
        )
    text = WORKFLOW_PATH.read_text()
    match = re.search(r'SHARD_A_DIRS="([^"]+)"', text)
    if not match:
        pytest.skip(
            "SHARD_A_DIRS not found in tests.yml — skipping shard config tests",
            allow_module_level=True,
        )
    return match.group(1).split()


SHARD_A_DIRS = _parse_shard_a_dirs_from_workflow()


class TestCIShardConfig:
    def test_shard_a_directories_exist(self) -> None:
        for d in SHARD_A_DIRS:
            assert (REPO_ROOT / d).is_dir(), f"Shard A directory {d} does not exist"

    def test_shard_a_directories_contain_tests(self) -> None:
        for d in SHARD_A_DIRS:
            test_files = list((REPO_ROOT / d).rglob("test_*.py"))
            assert test_files, f"Shard A directory {d} contains no test files"

    def test_known_general_dirs_exist(self) -> None:
        all_test_dirs = {
            p.name
            for p in (REPO_ROOT / "tests").iterdir()
            if p.is_dir() and not p.name.startswith("_") and not p.name.startswith(".")
        }
        stale = KNOWN_GENERAL_SHARD_DIRS - all_test_dirs
        assert not stale, (
            f"KNOWN_GENERAL_SHARD_DIRS contains stale entries: {stale}. "
            "Remove entries that no longer exist under tests/."
        )

    def test_all_test_dirs_assigned_to_shard(self) -> None:
        all_test_dirs = {
            p.name
            for p in (REPO_ROOT / "tests").iterdir()
            if p.is_dir() and not p.name.startswith("_") and not p.name.startswith(".")
        }
        shard_a_names = {d.removeprefix("tests/") for d in SHARD_A_DIRS}
        assigned = shard_a_names | KNOWN_GENERAL_SHARD_DIRS
        unassigned = all_test_dirs - assigned
        assert not unassigned, (
            f"Test directories not assigned to any shard: {unassigned}. "
            "Add to SHARD_A_DIRS in tests.yml or KNOWN_GENERAL_SHARD_DIRS in this file."
        )
