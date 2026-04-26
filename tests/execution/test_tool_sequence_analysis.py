from __future__ import annotations

import json
import pathlib
from collections import Counter

import pytest

from autoskillit.execution.tool_sequence_analysis import (
    AnalysisResult,
    DFG,
    GapStats,
    TurnSequence,
    build_dfg,
    build_dfg_by_recipe,
    compute_gap_stats,
    parse_raw_cc_jsonl,
    parse_sessions_from_summary_dir,
    render_adjacency_table,
    render_dot,
    render_mermaid,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# TestParseRawCCJsonl
# ---------------------------------------------------------------------------


class TestParseRawCCJsonl:
    def test_extracts_tool_names_from_assistant_content(self, tmp_path: pathlib.Path) -> None:
        record = {
            "type": "assistant",
            "requestId": "r1",
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "name": "ToolA"},
                    {"type": "tool_use", "name": "ToolB"},
                ]
            },
        }
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps(record) + "\n")
        result = parse_raw_cc_jsonl(log)
        assert result == [["ToolA", "ToolB"]]

    def test_deduplicates_same_request_id(self, tmp_path: pathlib.Path) -> None:
        record = {
            "type": "assistant",
            "requestId": "dup",
            "message": {"content": [{"type": "tool_use", "name": "ToolA"}]},
        }
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")
        result = parse_raw_cc_jsonl(log)
        assert len(result) == 1

    def test_cap_applied_at_n_8(self, tmp_path: pathlib.Path) -> None:
        tools = [{"type": "tool_use", "name": f"Tool{i}"} for i in range(12)]
        record = {
            "type": "assistant",
            "requestId": "r1",
            "message": {"content": tools},
        }
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps(record) + "\n")
        result = parse_raw_cc_jsonl(log)
        assert len(result[0]) == 8

    def test_skips_records_not_of_type_assistant(self, tmp_path: pathlib.Path) -> None:
        records = [
            {"type": "user", "requestId": "r1", "message": {"content": [{"type": "tool_use", "name": "X"}]}},
            {"type": "tool_result", "requestId": "r2", "message": {"content": [{"type": "tool_use", "name": "Y"}]}},
        ]
        log = tmp_path / "session.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        result = parse_raw_cc_jsonl(log)
        assert result == []

    def test_skips_tool_use_blocks_with_empty_name(self, tmp_path: pathlib.Path) -> None:
        record = {
            "type": "assistant",
            "requestId": "r1",
            "message": {
                "content": [
                    {"type": "tool_use", "name": ""},
                    {"type": "tool_use"},
                    {"type": "tool_use", "name": "ValidTool"},
                ]
            },
        }
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps(record) + "\n")
        result = parse_raw_cc_jsonl(log)
        assert result == [["ValidTool"]]

    def test_empty_jsonl_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        log = tmp_path / "session.jsonl"
        log.write_text("")
        result = parse_raw_cc_jsonl(log)
        assert result == []

    def test_malformed_json_line_skipped(self, tmp_path: pathlib.Path) -> None:
        good = json.dumps({
            "type": "assistant",
            "requestId": "r1",
            "message": {"content": [{"type": "tool_use", "name": "GoodTool"}]},
        })
        log = tmp_path / "session.jsonl"
        log.write_text("NOT_JSON\n" + good + "\n")
        result = parse_raw_cc_jsonl(log)
        assert result == [["GoodTool"]]

    def test_message_field_absent_returns_empty_turn(self, tmp_path: pathlib.Path) -> None:
        record = {"type": "assistant", "requestId": "r1"}
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps(record) + "\n")
        result = parse_raw_cc_jsonl(log)
        assert result == [[]]


# ---------------------------------------------------------------------------
# TestBuildDFG
# ---------------------------------------------------------------------------


class TestBuildDFG:
    def _make_seq(self, turns: list[list[str]], recipe: str = "") -> TurnSequence:
        return TurnSequence(session_id="s1", recipe_name=recipe, turns=turns)

    def test_bigram_counts_across_turns(self) -> None:
        sessions = [self._make_seq([["A", "B"], ["B", "C"]])]
        dfg = build_dfg(sessions)
        assert dfg.bigrams[("A", "B")] == 1
        assert dfg.bigrams[("B", "B")] == 1
        assert dfg.bigrams[("B", "C")] == 1

    def test_ngrams_mined_within_single_turn(self) -> None:
        sessions = [self._make_seq([["A", "B", "C"]])]
        dfg = build_dfg(sessions)
        assert ("A", "B") in dfg.ngrams
        assert ("B", "C") in dfg.ngrams
        assert ("A", "B", "C") in dfg.ngrams

    def test_ngrams_max_length_5(self) -> None:
        sessions = [self._make_seq([["A", "B", "C", "D", "E", "F"]])]
        dfg = build_dfg(sessions)
        for key in dfg.ngrams:
            assert len(key) <= 5

    def test_gap_analysis_correct_turn_distance(self) -> None:
        sessions = [self._make_seq([["A"], [], [], ["B"]])]
        dfg = build_dfg(sessions)
        assert ("A", "B") in dfg.pair_gaps
        assert 3 in dfg.pair_gaps[("A", "B")]

    def test_gap_analysis_multiple_sessions_aggregated(self) -> None:
        s1 = TurnSequence(session_id="s1", recipe_name="", turns=[["A"], [], ["B"]])
        s2 = TurnSequence(session_id="s2", recipe_name="", turns=[["A"], [], ["B"]])
        dfg = build_dfg([s1, s2])
        assert dfg.pair_gaps[("A", "B")] == [2, 2]

    def test_empty_sessions_list_returns_zero_dfg(self) -> None:
        dfg = build_dfg([])
        assert len(dfg.bigrams) == 0
        assert len(dfg.ngrams) == 0
        assert len(dfg.pair_gaps) == 0
        assert dfg.total_turns == 0


class TestBuildDFGByRecipe:
    def test_stratification_separates_recipes(self) -> None:
        s1 = TurnSequence(session_id="s1", recipe_name="lint", turns=[["A", "B"]])
        s2 = TurnSequence(session_id="s2", recipe_name="review", turns=[["C", "D"]])
        result = build_dfg_by_recipe([s1, s2])
        assert "lint" in result
        assert "review" in result
        assert ("A", "B") in result["lint"].bigrams
        assert ("A", "B") not in result["review"].bigrams

    def test_global_aggregate_includes_all_sessions(self) -> None:
        s1 = TurnSequence(session_id="s1", recipe_name="lint", turns=[["A", "B"]])
        s2 = TurnSequence(session_id="s2", recipe_name="review", turns=[["C", "D"]])
        from autoskillit.execution.tool_sequence_analysis import compute_analysis
        result = compute_analysis([s1, s2])
        assert ("A", "B") in result.global_dfg.bigrams
        assert ("C", "D") in result.global_dfg.bigrams


class TestComputeGapStats:
    def test_median_and_percentiles_correct(self) -> None:
        stats = compute_gap_stats([1, 2, 3, 4, 5])
        assert stats.median == 3.0
        assert stats.p25 == 2.0
        assert stats.p75 == 4.0
        assert stats.maximum == 5

    def test_single_gap_all_stats_equal(self) -> None:
        stats = compute_gap_stats([7])
        assert stats.median == 7.0
        assert stats.p25 == 7.0
        assert stats.p75 == 7.0
        assert stats.maximum == 7


# ---------------------------------------------------------------------------
# TestRenderMermaid
# ---------------------------------------------------------------------------


class TestRenderMermaid:
    def _make_dfg(self, bigrams: dict[tuple[str, str], int]) -> DFG:
        return DFG(bigrams=Counter(bigrams), ngrams=Counter(), pair_gaps={}, total_turns=1)

    def test_output_starts_with_flowchart_lr(self) -> None:
        dfg = self._make_dfg({("A", "B"): 1})
        output = render_mermaid(dfg)
        first_line = output.splitlines()[0]
        assert first_line.startswith("flowchart LR")

    def test_bigram_edges_present_in_output(self) -> None:
        dfg = self._make_dfg({("A", "B"): 5})
        output = render_mermaid(dfg)
        assert "A" in output
        assert "B" in output
        assert "5" in output

    def test_empty_dfg_renders_without_error(self) -> None:
        dfg = DFG(bigrams=Counter(), ngrams=Counter(), pair_gaps={}, total_turns=0)
        output = render_mermaid(dfg)
        assert len(output) > 0


# ---------------------------------------------------------------------------
# TestRenderAdjacencyTable
# ---------------------------------------------------------------------------


class TestRenderAdjacencyTable:
    def _make_dfg(self, bigrams: dict[tuple[str, str], int]) -> DFG:
        return DFG(bigrams=Counter(bigrams), ngrams=Counter(), pair_gaps={}, total_turns=1)

    def test_table_has_header_row(self) -> None:
        dfg = self._make_dfg({("A", "B"): 1})
        output = render_adjacency_table(dfg)
        lower = output.lower()
        assert "tool_a" in lower
        assert "tool_b" in lower

    def test_top_n_limits_rows(self) -> None:
        bigrams = {(f"A{i}", f"B{i}"): i for i in range(10, 0, -1)}
        dfg = self._make_dfg(bigrams)
        output = render_adjacency_table(dfg, top_n=3)
        # header + sep + 3 data rows = 5 lines
        data_lines = [l for l in output.splitlines() if l and not l.startswith("-")]
        # subtract 1 for header
        assert len(data_lines) - 1 <= 3


# ---------------------------------------------------------------------------
# TestRenderDot
# ---------------------------------------------------------------------------


class TestRenderDot:
    def _make_dfg(self, bigrams: dict[tuple[str, str], int]) -> DFG:
        return DFG(bigrams=Counter(bigrams), ngrams=Counter(), pair_gaps={}, total_turns=1)

    def test_dot_output_starts_with_digraph(self) -> None:
        dfg = self._make_dfg({("A", "B"): 1})
        output = render_dot(dfg)
        assert output.startswith("digraph")

    def test_dot_edge_correct_format(self) -> None:
        dfg = self._make_dfg({("A", "B"): 3})
        output = render_dot(dfg)
        assert '"A" -> "B"' in output
        assert "3" in output


# ---------------------------------------------------------------------------
# TestParseSessionsFromSummaryDir
# ---------------------------------------------------------------------------


class TestParseSessionsFromSummaryDir:
    def test_yields_turn_sequences_from_summary_json(self, tmp_path: pathlib.Path) -> None:
        session_dir = tmp_path / "sessions" / "abc123"
        session_dir.mkdir(parents=True)
        summary = {
            "turn_tool_calls": [["ToolA", "ToolB"], ["ToolC"]],
            "recipe_name": "test-recipe",
            "turn_timestamps": ["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"],
        }
        (session_dir / "summary.json").write_text(json.dumps(summary))
        sessions = list(parse_sessions_from_summary_dir(tmp_path))
        assert len(sessions) == 1
        assert sessions[0].session_id == "abc123"
        assert sessions[0].recipe_name == "test-recipe"
        assert sessions[0].turns == [["ToolA", "ToolB"], ["ToolC"]]

    def test_skips_summary_without_turn_tool_calls(self, tmp_path: pathlib.Path) -> None:
        session_dir = tmp_path / "sessions" / "old"
        session_dir.mkdir(parents=True)
        (session_dir / "summary.json").write_text(json.dumps({"recipe_name": "r"}))
        sessions = list(parse_sessions_from_summary_dir(tmp_path))
        assert sessions == []

    def test_skips_malformed_summary_json(self, tmp_path: pathlib.Path) -> None:
        session_dir = tmp_path / "sessions" / "bad"
        session_dir.mkdir(parents=True)
        (session_dir / "summary.json").write_text("NOT_JSON")
        sessions = list(parse_sessions_from_summary_dir(tmp_path))
        assert sessions == []
