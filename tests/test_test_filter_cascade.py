"""REQ-FILT-003: cascade entries for planner, _llm_triage, smoke_utils, version."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_filter import FilterMode, build_test_scope


class TestCascadeNewEntries:
    """REQ-FILT-003: four new packages must not force a full test run."""

    @pytest.mark.parametrize(
        "filepath,mode,dirs_to_create",
        [
            # Conservative: planner only touches its own tests
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.CONSERVATIVE,
                ["planner"],
            ),
            # Conservative: _llm_triage cascades into execution/server/recipe
            (
                "src/autoskillit/_llm_triage.py",
                FilterMode.CONSERVATIVE,
                ["execution", "server", "recipe"],
            ),
            # Conservative: smoke_utils cascades into recipe
            (
                "src/autoskillit/smoke_utils.py",
                FilterMode.CONSERVATIVE,
                ["recipe"],
            ),
            # Conservative: version cascades into server
            (
                "src/autoskillit/version.py",
                FilterMode.CONSERVATIVE,
                ["server"],
            ),
            # Aggressive: each package scoped to its own direct tests only
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.AGGRESSIVE,
                ["planner"],
            ),
            (
                "src/autoskillit/_llm_triage.py",
                FilterMode.AGGRESSIVE,
                [],
            ),
            (
                "src/autoskillit/smoke_utils.py",
                FilterMode.AGGRESSIVE,
                [],
            ),
            (
                "src/autoskillit/version.py",
                FilterMode.AGGRESSIVE,
                [],
            ),
        ],
    )
    def test_cascade_new_entries_not_full_run(
        self,
        tmp_path: Path,
        filepath: str,
        mode: FilterMode,
        dirs_to_create: list[str],
    ) -> None:
        tests_root = tmp_path / "tests"
        for d in dirs_to_create:
            (tests_root / d).mkdir(parents=True, exist_ok=True)

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
