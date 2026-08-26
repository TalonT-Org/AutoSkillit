"""Tests for load_coverage_map in tests/_test_filter.py."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from tests import _test_filter as test_filter
from tests._test_filter import load_coverage_map

pytestmark = [pytest.mark.medium]


def _envelope(source_map: object, *, pytest_exit_code: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "provenance": {"pytest_exit_code": pytest_exit_code},
        "map": source_map,
    }


_REJECTION_CASES = {
    "MISSING": None,
    "UNREADABLE": "directory",
    "MALFORMED_JSON": "{bad json}",
    "NOT_AN_OBJECT": [],
    "UNKNOWN_SCHEMA_VERSION": _envelope({}, pytest_exit_code=0) | {"schema_version": 2},
    "MISSING_PROVENANCE": {"schema_version": 1, "map": {}},
    "PRODUCER_FAILED": _envelope({}, pytest_exit_code=1),
    "STALE": _envelope({}),
    "MALFORMED_ENTRY": _envelope([]),
}


class TestLoadCoverageMap:
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
        result = load_coverage_map(map_file)
        assert result is not None
        assert "src/autoskillit/recipe/rules_dataflow.py" in result
        assert isinstance(result["src/autoskillit/recipe/rules_dataflow.py"], set)
        assert (
            "tests/recipe/test_rules_dataflow.py"
            in result["src/autoskillit/recipe/rules_dataflow.py"]
        )

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when the map file does not exist."""
        result = load_coverage_map(tmp_path / "nonexistent.json")
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
        result = load_coverage_map(map_file, max_age_days=30)
        assert result is None

    def test_fresh_file_returns_data(self, tmp_path: Path) -> None:
        """Returns data when the file mtime is within max_age_days."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text(
            json.dumps(_envelope({"src/foo.py": ["tests/test_foo.py"]})), encoding="utf-8"
        )
        result = load_coverage_map(map_file, max_age_days=30)
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
        assert load_coverage_map(map_file, max_age_days=1) is None
        result_fresh = load_coverage_map(map_file, max_age_days=3)
        assert result_fresh is not None
        assert result_fresh["src/foo.py"] == {"tests/test_foo.py"}

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """Returns None on JSON parse failure."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text("{bad json}", encoding="utf-8")
        result = load_coverage_map(map_file)
        assert result is None

    @pytest.mark.parametrize(("rejection_name", "payload"), _REJECTION_CASES.items())
    def test_every_source_map_rejection_is_reachable_and_fails_open(
        self, tmp_path: Path, rejection_name: str, payload: object
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

        rejection = test_filter.SourceMapRejection[rejection_name]
        assert set(test_filter.SourceMapRejection.__members__) == set(_REJECTION_CASES)
        detail = test_filter._REJECTION_DETAIL[rejection]
        with pytest.warns(UserWarning, match=re.escape(detail)):
            assert load_coverage_map(map_file) is None

    def test_load_coverage_map_rejects_legacy_unenveloped_map(self, tmp_path: Path) -> None:
        """The pre-envelope map format fails open instead of narrowing Step 7."""
        map_file = tmp_path / "test-source-map.json"
        map_file.write_text('{"src/foo.py": ["tests/test_foo.py"]}', encoding="utf-8")

        rejection = test_filter.SourceMapRejection.MISSING_PROVENANCE
        with pytest.warns(UserWarning, match=re.escape(test_filter._REJECTION_DETAIL[rejection])):
            assert load_coverage_map(map_file) is None
