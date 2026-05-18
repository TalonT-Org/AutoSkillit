"""Tests for token_summary_hook v1 backward compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _write_v1_session(log_root: Path, dir_name: str, tu_data: dict, idx_data: dict) -> None:
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "token_usage.json").write_text(json.dumps(tu_data))
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(idx_data) + "\n")


def test_load_sessions_canonical_names_preferred(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    log_root.mkdir()

    idx_entry = {"dir_name": "s1", "kitchen_id": "k1", "timestamp": "2025-01-15T10:00:00+00:00"}
    tu_data = {
        "session_label": "implement",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_write_tokens": 25,
        "cache_read_tokens": 12,
        "cache_creation_input_tokens": 99,
        "cache_read_input_tokens": 99,
        "timing_seconds": 10.0,
    }
    _write_v1_session(log_root, "s1", tu_data, idx_entry)

    from autoskillit.hooks.token_summary_hook import _load_sessions

    result = _load_sessions(log_root, "k1")

    assert result["implement"]["cache_write_tokens"] == 25
    assert result["implement"]["cache_read_tokens"] == 12


class TestSessionsJsonlV1BackwardCompat:
    def test_v1_pipeline_id_entry_matches_kitchen_id_filter(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        idx_entry = {
            "dir_name": "old1",
            "pipeline_id": "legacy-k",
            "timestamp": "2025-06-01T00:00:00",
        }
        tu_data = {
            "session_label": "plan",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 5,
            "timing_seconds": 8.0,
        }
        _write_v1_session(log_root, "old1", tu_data, idx_entry)

        from autoskillit.hooks.token_summary_hook import _load_sessions

        result = _load_sessions(log_root, "legacy-k")

        assert len(result) == 1

    def test_v1_entry_without_order_id_skipped_on_order_filter(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        v1_entry = {
            "dir_name": "v1session",
            "pipeline_id": "k1",
            "timestamp": "2025-06-01T00:00:00",
        }
        v1_tu = {
            "session_label": "plan",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "timing_seconds": 8.0,
        }
        v2_entry = {
            "dir_name": "v2session",
            "kitchen_id": "k1",
            "order_id": "target",
            "timestamp": "2025-06-01T00:00:00",
        }
        v2_tu = {
            "session_label": "implement",
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_write_tokens": 20,
            "timing_seconds": 10.0,
        }
        _write_v1_session(log_root, "v1session", v1_tu, v1_entry)
        _write_v1_session(log_root, "v2session", v2_tu, v2_entry)

        from autoskillit.hooks.token_summary_hook import _load_sessions

        result = _load_sessions(log_root, "k1", order_id="target")

        assert len(result) == 1
        assert "implement" in result

    def test_mixed_v1_v2_entries_load_without_error(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        v1_entry = {
            "dir_name": "v1s",
            "pipeline_id": "k1",
            "timestamp": "2025-06-01T00:00:00",
        }
        v1_tu = {
            "session_label": "plan",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "timing_seconds": 8.0,
        }
        v2_entry = {
            "dir_name": "v2s",
            "kitchen_id": "k1",
            "order_id": "o1",
            "timestamp": "2025-06-01T00:00:00",
        }
        v2_tu = {
            "session_label": "implement",
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_write_tokens": 20,
            "timing_seconds": 10.0,
        }
        _write_v1_session(log_root, "v1s", v1_tu, v1_entry)
        _write_v1_session(log_root, "v2s", v2_tu, v2_entry)

        from autoskillit.hooks.token_summary_hook import _load_sessions

        result = _load_sessions(log_root, "k1")

        assert len(result) == 2

    def test_v1_token_values_accumulate_correctly(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        for i in range(2):
            idx = {
                "dir_name": f"v1s{i}",
                "pipeline_id": "k1",
                "timestamp": "2025-06-01T00:00:00",
            }
            tu = {
                "session_label": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
                "timing_seconds": 8.0,
            }
            _write_v1_session(log_root, f"v1s{i}", tu, idx)

        from autoskillit.hooks.token_summary_hook import _load_sessions

        result = _load_sessions(log_root, "k1")

        assert result["plan"]["cache_write_tokens"] == 20
        assert result["plan"]["invocation_count"] == 2

    @pytest.mark.parametrize(
        "_filter_type,filter_kwargs,idx_entry",
        [
            (
                "cwd_filter",
                {"cwd_filter": "/worktree/a"},
                {"dir_name": "s1", "cwd": "/worktree/a", "timestamp": "2025-06-01T00:00:00"},
            ),
            (
                "kitchen_id_filter",
                {"kitchen_id_filter": "k1"},
                {
                    "dir_name": "s1",
                    "kitchen_id": "k1",
                    "timestamp": "2025-06-01T00:00:00",
                },
            ),
            (
                "campaign_id_filter",
                {"campaign_id_filter": "camp1"},
                {
                    "dir_name": "s1",
                    "campaign_id": "camp1",
                    "timestamp": "2025-06-01T00:00:00",
                },
            ),
            (
                "order_id_filter",
                {"order_id_filter": "ord1"},
                {
                    "dir_name": "s1",
                    "order_id": "ord1",
                    "timestamp": "2025-06-01T00:00:00",
                },
            ),
            (
                "dispatch_id_filter",
                {"dispatch_id_filter": "disp1"},
                {
                    "dir_name": "s1",
                    "dispatch_id": "disp1",
                    "timestamp": "2025-06-01T00:00:00",
                },
            ),
        ],
    )
    def test_each_filter_type_with_v1_entries(
        self, tmp_path: Path, _filter_type: str, filter_kwargs: dict, idx_entry: dict
    ) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        tu_data = {
            "session_label": "plan",
            "input_tokens": 100,
            "output_tokens": 50,
            "timing_seconds": 8.0,
        }
        _write_v1_session(log_root, idx_entry["dir_name"], tu_data, idx_entry)

        from autoskillit.pipeline.audit import _iter_session_log_entries

        results = list(
            _iter_session_log_entries(log_root, "", "token_usage.json", **filter_kwargs)
        )

        assert len(results) == 1

    def test_each_filter_type_since_filter(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        idx_entry = {
            "dir_name": "s1",
            "timestamp": "2025-06-01T00:00:00",
        }
        tu_data = {
            "session_label": "plan",
            "input_tokens": 100,
            "output_tokens": 50,
            "timing_seconds": 8.0,
        }
        _write_v1_session(log_root, "s1", tu_data, idx_entry)

        from autoskillit.pipeline.audit import _iter_session_log_entries

        results = list(
            _iter_session_log_entries(log_root, "2025-05-01T00:00:00", "token_usage.json")
        )

        assert len(results) == 1

    def test_iter_session_log_entries_non_matching_filter(self, tmp_path: Path) -> None:
        log_root = tmp_path / "logs"
        log_root.mkdir()

        idx_entry = {
            "dir_name": "s1",
            "cwd": "/worktree/a",
            "timestamp": "2025-06-01T00:00:00",
        }
        tu_data = {
            "session_label": "plan",
            "input_tokens": 100,
            "output_tokens": 50,
            "timing_seconds": 8.0,
        }
        _write_v1_session(log_root, "s1", tu_data, idx_entry)

        from autoskillit.pipeline.audit import _iter_session_log_entries

        results = list(
            _iter_session_log_entries(log_root, "", "token_usage.json", cwd_filter="/worktree/b")
        )

        assert len(results) == 0
