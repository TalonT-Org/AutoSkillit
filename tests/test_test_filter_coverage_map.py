"""Tests for load_coverage_map in tests/_test_filter.py."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests import _test_filter as test_filter
from tests._test_filter import load_coverage_map

pytestmark = [pytest.mark.medium]

SOURCE_COMMIT = "a" * 40


def _envelope(
    source_map: object, *, pytest_exit_code: int = 0, source_commit: object = SOURCE_COMMIT
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "provenance": {"pytest_exit_code": pytest_exit_code, "source_commit": source_commit},
        "map": source_map,
    }


_REJECTION_CASES = {
    "MISSING": None,
    "UNREADABLE": "directory",
    "MALFORMED_JSON": "{bad json}",
    "NOT_AN_OBJECT": [],
    "UNKNOWN_SCHEMA_VERSION": _envelope({}, pytest_exit_code=0) | {"schema_version": 3},
    "MISSING_PROVENANCE": {"schema_version": 1, "map": {}},
    "PRODUCER_FAILED": _envelope({}, pytest_exit_code=1),
    "STALE": _envelope({}),
    "MALFORMED_ENTRY": _envelope([]),
    "NOT_ANCESTOR": _envelope({}),
}


class TestLoadCoverageMap:
    @pytest.fixture(autouse=True)
    def accept_source_lineage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            test_filter.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
        )

    def test_returns_dict_from_valid_json(self, tmp_path: Path) -> None:
        """Parses a valid JSON map and returns dict[str, set[str]]."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(
                _envelope(
                    {
                        "src/autoskillit/recipe/rules_dataflow.py": [
                            "tests/recipe/test_rules_dataflow.py",
                            "tests/recipe/test_rules_structure.py",
                        ]
                    }
                )
            ),
            encoding="utf-8",
        )
        result = load_coverage_map(map_file, cwd=tmp_path)
        assert result is not None
        assert "src/autoskillit/recipe/rules_dataflow.py" in result
        assert isinstance(result["src/autoskillit/recipe/rules_dataflow.py"], set)
        assert (
            "tests/recipe/test_rules_dataflow.py"
            in result["src/autoskillit/recipe/rules_dataflow.py"]
        )

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when the map file does not exist."""
        result = load_coverage_map(tmp_path / "nonexistent.json", cwd=tmp_path)
        assert result is None

    def test_stale_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when the file mtime is older than max_age_days."""
        import os
        import time

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )
        # Backdate mtime by 31 days
        old_mtime = time.time() - (31 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))
        result = load_coverage_map(map_file, max_age_days=30, cwd=tmp_path)
        assert result is None

    def test_fresh_file_returns_data(self, tmp_path: Path) -> None:
        """Returns data when the file mtime is within max_age_days."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )
        result = load_coverage_map(map_file, max_age_days=30, cwd=tmp_path)
        assert result is not None
        assert result["src/foo.py"] == {"tests/test_foo.py"}

    def test_custom_max_age_days_respected(self, tmp_path: Path) -> None:
        """Custom max_age_days threshold is respected."""
        import os
        import time

        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )
        # Backdate by 2 days — stale with max_age_days=1, fresh with max_age_days=3
        old_mtime = time.time() - (2 * 24 * 3600)
        os.utime(map_file, (old_mtime, old_mtime))
        assert load_coverage_map(map_file, max_age_days=1, cwd=tmp_path) is None
        result_fresh = load_coverage_map(map_file, max_age_days=3, cwd=tmp_path)
        assert result_fresh is not None
        assert result_fresh["src/foo.py"] == {"tests/test_foo.py"}

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """Returns None on JSON parse failure."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text("{bad json}", encoding="utf-8")
        result = load_coverage_map(map_file, cwd=tmp_path)
        assert result is None

    @pytest.mark.parametrize(("rejection_name", "payload"), _REJECTION_CASES.items())
    def test_every_source_map_rejection_is_reachable_and_fails_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rejection_name: str, payload: object
    ) -> None:
        """Every classified rejection preserves the coarser test-filter selection."""
        map_file = tmp_path / "test-source-map.json"
        if payload == "directory":
            map_file.mkdir()
        elif payload is not None:
            content = payload if isinstance(payload, str) else json.dumps(payload)
            map_file.write_text(content, encoding="utf-8")
        if rejection_name == "STALE":
            old_mtime = time.time() - (31 * 24 * 3600)
            os.utime(map_file, (old_mtime, old_mtime))
        if rejection_name == "NOT_ANCESTOR":
            monkeypatch.setattr(
                test_filter.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess(args, 1),
            )

        rejection = test_filter.SourceMapRejection[rejection_name]
        assert set(test_filter.SourceMapRejection.__members__) == set(_REJECTION_CASES)
        detail = test_filter._REJECTION_DETAIL[rejection]
        with pytest.warns(UserWarning, match=re.escape(detail)):
            assert load_coverage_map(map_file, cwd=tmp_path) is None

    def test_schema_v2_with_unobservable_sources_loads(self, tmp_path: Path) -> None:
        """A v2 artifact loads through the unchanged v1 per-entry parser.

        unobservable_sources is a sibling top-level key the consumer never reads —
        that is the point of the shape choice: no per-entry parsing change needed.
        """
        map_file = tmp_path / "test-source-map.json"
        payload = _envelope({"src/foo.py": ["tests/test_foo.py"]})
        payload["schema_version"] = 2
        payload["unobservable_sources"] = [
            {"path": "src/bar.py", "reason": "not_measured"},
            {"path": "src/baz.py", "reason": "attributed_only_by_fixture"},
        ]
        map_file.write_text(json.dumps(payload), encoding="utf-8")
        result = load_coverage_map(map_file, cwd=tmp_path)
        assert result == {"src/foo.py": {"tests/test_foo.py"}}

    def test_schema_v1_still_loads(self, tmp_path: Path) -> None:
        """A v1 artifact (no unobservable_sources key) still loads successfully."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )
        result = load_coverage_map(map_file, cwd=tmp_path)
        assert result == {"src/foo.py": {"tests/test_foo.py"}}

    def test_load_coverage_map_rejects_legacy_unenveloped_map(self, tmp_path: Path) -> None:
        """The pre-envelope map format fails open instead of narrowing Step 7."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text('{"src/foo.py": ["tests/test_foo.py"]}', encoding="utf-8")

        rejection = test_filter.SourceMapRejection.MISSING_PROVENANCE
        with pytest.warns(UserWarning, match=re.escape(test_filter._REJECTION_DETAIL[rejection])):
            assert load_coverage_map(map_file, cwd=tmp_path) is None

    @pytest.mark.parametrize("source_commit", [None, "", "HEAD", "a" * 39, "A" * 40, "g" * 40, 7])
    def test_invalid_source_commit_rejects_provenance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_commit: object
    ) -> None:
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({}, source_commit=source_commit)), encoding="utf-8"
        )

        run = Mock()
        monkeypatch.setattr(test_filter.subprocess, "run", run)
        rejection = test_filter.SourceMapRejection.MISSING_PROVENANCE
        with pytest.warns(UserWarning, match=re.escape(test_filter._REJECTION_DETAIL[rejection])):
            assert load_coverage_map(map_file, cwd=tmp_path) is None
        run.assert_not_called()

    def test_missing_source_commit_rejects_before_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        map_file = tmp_path / "test-source-map.json"
        payload = _envelope({})
        provenance = payload["provenance"]
        assert isinstance(provenance, dict)
        provenance.pop("source_commit")
        map_file.write_text(json.dumps(payload), encoding="utf-8")
        run = Mock()
        monkeypatch.setattr(test_filter.subprocess, "run", run)
        with pytest.warns(UserWarning, match="missing successful provenance"):
            assert load_coverage_map(map_file, cwd=tmp_path) is None
        run.assert_not_called()

    def test_ancestor_check_uses_source_commit_and_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )
        calls: list[tuple[object, dict[str, object]]] = []

        def record_ancestry(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(test_filter.subprocess, "run", record_ancestry)
        assert load_coverage_map(map_file, cwd=tmp_path) == {"src/foo.py": {"tests/test_foo.py"}}
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args == ["git", "merge-base", "--is-ancestor", SOURCE_COMMIT, "HEAD"]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 10

    @pytest.mark.parametrize(
        "failure", [128, OSError("git unavailable"), subprocess.TimeoutExpired("git", 10)]
    )
    def test_lineage_check_failure_keeps_valid_map(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: object
    ) -> None:
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )

        def fail_ancestry(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if isinstance(failure, BaseException):
                raise failure
            assert isinstance(failure, int)
            return subprocess.CompletedProcess(args, failure, stderr="git failed")

        monkeypatch.setattr(test_filter.subprocess, "run", fail_ancestry)
        with pytest.warns(
            UserWarning, match="lineage check failed|could not check source lineage"
        ):
            assert load_coverage_map(map_file, cwd=tmp_path) == {
                "src/foo.py": {"tests/test_foo.py"}
            }
