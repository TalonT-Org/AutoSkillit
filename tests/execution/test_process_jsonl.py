"""Tests for JSONL marker detection utilities.

These tests cover the structured JSONL parsing helpers used by the
session log monitor and heartbeat to detect completion markers and
record types without false-fires on embedded marker text.
"""

from __future__ import annotations

import json

from autoskillit.execution.process import (
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _marker_is_standalone,
)


class TestJsonlContainsMarker:
    """_jsonl_contains_marker performs structured record filtering."""

    def test_matches_in_allowed_record_type(self):
        content = json.dumps({"type": "assistant", "message": {"content": "Done\nMARKER"}})
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_ignores_disallowed_record_type(self):
        content = json.dumps({"type": "queue-operation", "content": "prompt\nMARKER"})
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_ignores_unparseable_lines(self):
        content = "not valid json MARKER\n"
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_multiline_mixed_records(self):
        lines = [
            json.dumps({"type": "user", "message": {"content": "hello\nMARKER"}}),
            json.dumps({"type": "assistant", "message": {"content": "world"}}),
            json.dumps({"type": "assistant", "message": {"content": "found it\nMARKER"}}),
        ]
        content = "\n".join(lines)
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_no_match_when_marker_absent(self):
        content = json.dumps({"type": "assistant", "message": {"content": "no marker here"}})
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))

    def test_marker_in_result_record(self):
        content = json.dumps({"type": "result", "result": "MARKER", "subtype": "success"})
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"result"}))

    def test_marker_embedded_in_prose_no_match(self):
        content = json.dumps(
            {
                "type": "assistant",
                "message": {"content": "I will emit MARKER when done"},
            }
        )
        assert not _jsonl_contains_marker(content, "MARKER", frozenset({"assistant"}))


class TestMarkerIsStandalone:
    """_marker_is_standalone validates standalone line matching."""

    def test_standalone_marker(self):
        assert _marker_is_standalone(
            "Done\n\n%%AUTOSKILLIT_COMPLETE%%", "%%AUTOSKILLIT_COMPLETE%%"
        )

    def test_embedded_marker_rejected(self):
        assert not _marker_is_standalone(
            "I will emit %%AUTOSKILLIT_COMPLETE%% when done", "%%AUTOSKILLIT_COMPLETE%%"
        )

    def test_marker_as_sole_content(self):
        assert _marker_is_standalone("%%AUTOSKILLIT_COMPLETE%%", "%%AUTOSKILLIT_COMPLETE%%")

    def test_marker_with_trailing_whitespace(self):
        assert _marker_is_standalone(
            "Done\n%%AUTOSKILLIT_COMPLETE%%  ", "%%AUTOSKILLIT_COMPLETE%%"
        )


class TestJsonlFieldLevelMarkerMatching:
    """_jsonl_contains_marker extracts field values, not raw JSON lines."""

    def test_marker_quoted_in_assistant_prose_no_match(self):
        """Marker text quoted in prose should NOT trigger detection."""
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "I see %%AUTOSKILLIT_COMPLETE%% in the prompt",
                },
            }
        )
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_marker_as_standalone_final_line_matches(self):
        """Marker as standalone final line in content should trigger detection."""
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "Task done.\n\n%%AUTOSKILLIT_COMPLETE%%",
                },
            }
        )
        assert _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_marker_in_result_record_matches(self):
        """Marker in result record's result field should trigger detection."""
        content = json.dumps(
            {
                "type": "result",
                "result": "%%AUTOSKILLIT_COMPLETE%%",
                "subtype": "success",
            }
        )
        assert _jsonl_contains_marker(content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"result"}))


class TestJsonlContainsMarkerContentBlocks:
    """_jsonl_contains_marker handles list-of-content-blocks format."""

    def test_list_content_blocks_with_marker(self):
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Done\n%%AUTOSKILLIT_COMPLETE%%"}]
                },
            }
        )
        assert _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_list_content_mixed_blocks_with_marker(self):
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Running..."},
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                        {"type": "text", "text": "\n%%AUTOSKILLIT_COMPLETE%%"},
                    ]
                },
            }
        )
        assert _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_list_content_marker_embedded_in_prose(self):
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I will emit %%AUTOSKILLIT_COMPLETE%% when done"}
                    ]
                },
            }
        )
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_list_content_no_marker(self):
        content = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "still working"}]},
            }
        )
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_none_content_no_crash(self):
        content = json.dumps({"type": "assistant", "message": {"content": None}})
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )

    def test_none_message_no_crash(self):
        content = json.dumps({"type": "assistant", "message": None})
        assert not _jsonl_contains_marker(
            content, "%%AUTOSKILLIT_COMPLETE%%", frozenset({"assistant"})
        )


class TestJsonlContainsMarkerEdgeCases:
    """Edge cases: empty input and partial/truncated NDJSON."""

    def test_empty_string_returns_false(self):
        """Empty input contains no records → False."""
        assert not _jsonl_contains_marker("", "MARKER", frozenset({"assistant"}))

    def test_partial_truncated_json_skipped_valid_line_matches(self):
        """Partial JSON from a mid-write kill is skipped; valid subsequent line matches."""
        # Simulate: process killed mid-write on first line, second line is complete.
        truncated = '{"type": "result", "result": "MARKER", "subtype": "succe'
        valid = json.dumps({"type": "result", "result": "MARKER", "subtype": "success"})
        content = truncated + "\n" + valid
        assert _jsonl_contains_marker(content, "MARKER", frozenset({"result"}))

    def test_only_truncated_json_returns_false(self):
        """Content with only a truncated JSON line (no valid lines) → False."""
        truncated = '{"type": "assistant", "message": {"content": "Done\nMARKER"'
        assert not _jsonl_contains_marker(truncated, "MARKER", frozenset({"assistant"}))


class TestJsonlHasRecordTypeResultContent:
    """_jsonl_has_record_type requires non-empty result field for type=result records."""

    def test_rejects_empty_result_field(self):
        """A type=result record with result="" must NOT satisfy _jsonl_has_record_type.

        Confirming on empty content is the source of the drain-race false negative.
        """
        empty_result_line = '{"type":"result","subtype":"success","result":"","is_error":false}\n'
        assert not _jsonl_has_record_type(empty_result_line, frozenset({"result"}))

    def test_accepts_nonempty_result_field(self):
        """Non-empty result still satisfies the predicate."""
        nonempty_line = '{"type":"result","subtype":"success","result":"done","is_error":false}\n'
        assert _jsonl_has_record_type(nonempty_line, frozenset({"result"}))

    def test_non_result_types_unaffected(self):
        """Non-result record types (e.g. assistant, system) are unaffected by the change."""
        assistant_line = (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n'
        )
        assert _jsonl_has_record_type(assistant_line, frozenset({"assistant"}))

    def test_result_field_none_rejected(self):
        """A type=result record with result=null must NOT satisfy the predicate."""
        null_result_line = '{"type":"result","subtype":"success","result":null,"is_error":false}\n'
        assert not _jsonl_has_record_type(null_result_line, frozenset({"result"}))

    def test_result_field_whitespace_only_rejected(self):
        """A type=result record with result='   ' (whitespace only) must NOT satisfy."""
        whitespace_line = '{"type":"result","subtype":"success","result":"   ","is_error":false}\n'
        assert not _jsonl_has_record_type(whitespace_line, frozenset({"result"}))

    def test_rejects_result_without_marker_when_marker_configured(self):
        """Non-empty result missing the marker must NOT confirm."""
        line = (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "Task completed.",
                    "is_error": False,
                }
            )
            + "\n"
        )
        assert not _jsonl_has_record_type(
            line, frozenset({"result"}), completion_marker="%%ORDER_UP%%"
        )

    def test_accepts_result_with_marker_when_marker_configured(self):
        """Non-empty result with marker as standalone line must confirm."""
        line = (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "Task completed.\n%%ORDER_UP%%",
                    "is_error": False,
                }
            )
            + "\n"
        )
        assert _jsonl_has_record_type(
            line, frozenset({"result"}), completion_marker="%%ORDER_UP%%"
        )

    def test_marker_empty_string_skips_marker_check(self):
        """Empty marker string means no marker check — backward-compatible."""
        line = (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "Task completed.",
                    "is_error": False,
                }
            )
            + "\n"
        )
        assert _jsonl_has_record_type(line, frozenset({"result"}), completion_marker="")

    def test_marker_must_be_standalone_line_in_result(self):
        """Marker embedded in prose (not standalone) must NOT confirm."""
        line = (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "I will emit %%ORDER_UP%% soon",
                    "is_error": False,
                }
            )
            + "\n"
        )
        assert not _jsonl_has_record_type(
            line, frozenset({"result"}), completion_marker="%%ORDER_UP%%"
        )
