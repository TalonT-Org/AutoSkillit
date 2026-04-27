"""REQ-FILT-003: cascade entries for planner, _llm_triage, smoke_utils, version."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_filter import FilterMode, build_test_scope


class TestCascadeNewEntries:
    """REQ-FILT-003: four new packages must not force a full test run."""

    @pytest.mark.parametrize(
        "filepath,mode,items_to_create,expected_in_result",
        [
            # Conservative: planner only touches its own tests
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.CONSERVATIVE,
                ["planner"],
                ["planner"],
            ),
            # Conservative: _llm_triage cascades into execution/server/recipe + direct test file
            (
                "src/autoskillit/_llm_triage.py",
                FilterMode.CONSERVATIVE,
                ["execution", "server", "recipe", "test_llm_triage.py"],
                ["execution", "server", "recipe", "test_llm_triage.py"],
            ),
            # Conservative: smoke_utils cascades into recipe + direct test file
            (
                "src/autoskillit/smoke_utils.py",
                FilterMode.CONSERVATIVE,
                ["recipe", "test_smoke_utils.py"],
                ["recipe", "test_smoke_utils.py"],
            ),
            # Conservative: version cascades into server + direct test file
            (
                "src/autoskillit/version.py",
                FilterMode.CONSERVATIVE,
                ["server", "test_version.py"],
                ["server", "test_version.py"],
            ),
            # Aggressive: planner scoped to its own tests directory
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.AGGRESSIVE,
                ["planner"],
                ["planner"],
            ),
            # Aggressive: _llm_triage scoped to its direct test file
            (
                "src/autoskillit/_llm_triage.py",
                FilterMode.AGGRESSIVE,
                ["test_llm_triage.py"],
                ["test_llm_triage.py"],
            ),
            # Aggressive: smoke_utils scoped to its direct test file
            (
                "src/autoskillit/smoke_utils.py",
                FilterMode.AGGRESSIVE,
                ["test_smoke_utils.py"],
                ["test_smoke_utils.py"],
            ),
            # Aggressive: version scoped to its direct test file
            (
                "src/autoskillit/version.py",
                FilterMode.AGGRESSIVE,
                ["test_version.py"],
                ["test_version.py"],
            ),
            # Conservative: quota_guard.py cascades into hooks tests + execution/test_quota.py
            (
                "src/autoskillit/hooks/quota_guard.py",
                FilterMode.CONSERVATIVE,
                ["hooks", "execution", "execution/test_quota.py"],
                ["hooks", "test_quota.py"],
            ),
        ],
    )
    def test_cascade_new_entries_not_full_run(
        self,
        tmp_path: Path,
        filepath: str,
        mode: FilterMode,
        items_to_create: list[str],
        expected_in_result: list[str],
    ) -> None:
        tests_root = tmp_path / "tests"
        tests_root.mkdir(parents=True, exist_ok=True)
        for item in items_to_create:
            if item.endswith(".py"):
                (tests_root / item).touch()
            else:
                (tests_root / item).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={filepath},
            mode=mode,
            tests_root=tests_root,
        )
        assert result is not None, (
            f"{filepath} still forces a full test run in {mode} mode — "
            "cascade entry is missing from LAYER_CASCADE_"
            + ("CONSERVATIVE" if mode == FilterMode.CONSERVATIVE else "AGGRESSIVE")
        )
        result_names = {p.name for p in result}
        for expected in expected_in_result:
            assert expected in result_names, (
                f"{expected!r} not found in result scope {result_names!r} "
                f"for {filepath} in {mode} mode"
            )
