# tests/infra/test_ci_shard_config.py
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]

SHARD_A_DIRS = [
    "tests/execution",
    "tests/contracts",
    "tests/core",
    "tests/planner",
    "tests/pipeline",
    "tests/migration",
    "tests/integration",
]


class TestCIShardConfig:
    def test_shard_a_directories_exist(self) -> None:
        for d in SHARD_A_DIRS:
            assert (REPO_ROOT / d).is_dir(), f"Shard A directory {d} does not exist"

    def test_shard_a_directories_contain_tests(self) -> None:
        for d in SHARD_A_DIRS:
            test_files = list((REPO_ROOT / d).rglob("test_*.py"))
            assert test_files, f"Shard A directory {d} contains no test files"

    def test_shard_a_directories_are_subset_of_test_dirs(self) -> None:
        all_test_dirs = {
            p.name
            for p in (REPO_ROOT / "tests").iterdir()
            if p.is_dir() and not p.name.startswith("_") and not p.name.startswith(".")
        }
        shard_a_names = {Path(d).name for d in SHARD_A_DIRS}
        extra = shard_a_names - all_test_dirs
        assert not extra, f"Shard A references non-existent test directories: {extra}"
