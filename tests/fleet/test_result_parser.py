"""Tests for fleet.result_parser — L3 result block parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet.result_parser import parse_l3_result_block

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

DISPATCH_ID = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"


def _open(dispatch_id: str = DISPATCH_ID) -> str:
    return f"---l3-result::{dispatch_id}---"


def _close(dispatch_id: str = DISPATCH_ID) -> str:
    return f"---end-l3-result::{dispatch_id}---"


def make_stdout(payload_json: str, dispatch_id: str = DISPATCH_ID) -> str:
    """Build a well-formed stdout string with sentinel block."""
    return (
        f"some prefix output\n{_open(dispatch_id)}\n"
        f"{payload_json}\n{_close(dispatch_id)}\nsome suffix"
    )


def make_jsonl_file(tmp_path, messages: list[str], filename: str = "session.jsonl") -> Path:
    """Write a JSONL file with type=assistant records containing given text."""
    path: Path = tmp_path / filename
    lines = []
    for text in messages:
        record = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": text}],
            },
        }
        lines.append(json.dumps(record))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_clean_parse_from_stdout() -> None:
    """Parse valid JSON body between properly formed sentinels."""

    payload = {"success": True, "value": 42}
    stdout = make_stdout(json.dumps(payload))

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_clean"
    assert result.payload == payload
    assert result.source == "stdout"
    assert result.parse_error is None


def test_last_occurrence_wins() -> None:
    """Parser must use the LAST occurrence of the sentinel block (rfind)."""

    first_payload = {"success": False, "value": "first"}
    second_payload = {"success": True, "value": "second"}
    stdout = (
        make_stdout(json.dumps(first_payload)) + "\n" + make_stdout(json.dumps(second_payload))
    )

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_clean"
    assert result.payload == second_payload


def test_mismatched_dispatch_id_rejected() -> None:
    """Sentinels with a different UUID must not be matched."""

    wrong_id = "00000000-0000-0000-0000-000000000000"
    stdout = make_stdout(json.dumps({"success": True}), dispatch_id=wrong_id)

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "no_sentinel"
    assert result.payload is None
    assert result.source == "stdout"


def test_ansi_codes_stripped_before_scan() -> None:
    """ANSI escape sequences around sentinel markers are stripped before scanning."""

    payload = {"success": True, "ansi": "ok"}
    raw_open = f"\x1b[1m{_open()}\x1b[0m"
    raw_close = f"\x1b[1m{_close()}\x1b[0m"
    stdout = f"prefix\n{raw_open}\n{json.dumps(payload)}\n{raw_close}\nsuffix"

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_clean"
    assert result.payload == payload
    assert result.source == "stdout"


def test_channel_b_fallback_recovers_truncated_stdout(tmp_path) -> None:
    """Channel B JSONL fallback recovers payload when stdout is truncated."""

    payload = {"success": True, "recovered": True}
    sentinel_text = f"{_open()}\n{json.dumps(payload)}\n{_close()}"
    jsonl_path = make_jsonl_file(tmp_path, [sentinel_text])

    result = parse_l3_result_block(
        stdout="truncated output with no sentinel",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=jsonl_path,
    )

    assert result.outcome == "completed_clean"
    assert result.payload == payload
    assert result.source == "assistant_messages_jsonl"


def test_empty_body_between_sentinels() -> None:
    """Opening and closing sentinels with no content yields completed_dirty."""

    stdout = f"prefix\n{_open()}\n{_close()}\nsuffix"

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_dirty"
    assert result.raw_body == ""
    assert result.parse_error is not None
    assert "empty" in result.parse_error


def test_invalid_json_body() -> None:
    """Sentinels present but body is malformed JSON yields completed_dirty."""

    stdout = make_stdout("not valid json {{{")

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_dirty"
    assert result.raw_body == "not valid json {{{"
    assert result.parse_error is not None


def test_no_sentinel_at_all() -> None:
    """Stdout with no sentinel markers and no JSONL path yields no_sentinel."""

    result = parse_l3_result_block(
        stdout="This output has absolutely no sentinel markers.",
        expected_dispatch_id=DISPATCH_ID,
    )

    assert result.outcome == "no_sentinel"
    assert result.payload is None
    assert result.source == "stdout"


def test_closing_before_opening_rejected() -> None:
    """Closing sentinel appearing before opening yields no_sentinel."""

    stdout = f"prefix\n{_close()}\nsome content\n{_open()}\nsuffix"

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "no_sentinel"


def test_bare_sentinel_without_id_ignored() -> None:
    """A sentinel like ---l3-result--- (no ::dispatch_id) must not be matched."""

    stdout = "prefix\n---l3-result---\n{}\n---end-l3-result---\nsuffix"

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "no_sentinel"


def test_multiple_occurrences_uses_last() -> None:
    """Three sentinel blocks — only the last payload is returned."""

    payloads = [
        {"order": 1, "success": False},
        {"order": 2, "success": False},
        {"order": 3, "success": True},
    ]
    stdout = "\n".join(make_stdout(json.dumps(p)) for p in payloads)

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_clean"
    assert result.payload == payloads[2]


def test_unicode_content_in_payload() -> None:
    """JSON body with Unicode characters (emoji, CJK) is preserved."""

    payload = {"success": True, "emoji": "🚀", "cjk": "日本語"}
    stdout = make_stdout(json.dumps(payload, ensure_ascii=False))

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_clean"
    assert result.payload == payload


def test_nested_triple_dashes_in_json_value() -> None:
    """JSON body with --- sequences inside string values does not confuse the parser."""

    payload = {"success": True, "value": "---this has --- dashes --- in it---"}
    stdout = make_stdout(json.dumps(payload))

    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)

    assert result.outcome == "completed_clean"
    assert result.payload == payload


def test_source_field_tracks_origin(tmp_path) -> None:
    """source field is 'stdout' when found in stdout, 'assistant_messages_jsonl' via JSONL."""

    payload = {"success": True}

    # (a) found in stdout
    stdout_result = parse_l3_result_block(
        stdout=make_stdout(json.dumps(payload)),
        expected_dispatch_id=DISPATCH_ID,
    )
    assert stdout_result.source == "stdout"

    # (b) found via JSONL fallback
    sentinel_text = f"{_open()}\n{json.dumps(payload)}\n{_close()}"
    jsonl_path = make_jsonl_file(tmp_path, [sentinel_text])
    jsonl_result = parse_l3_result_block(
        stdout="no sentinel here",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=jsonl_path,
    )
    assert jsonl_result.source == "assistant_messages_jsonl"


def test_channel_b_jsonl_file_missing(tmp_path) -> None:
    """Non-existent assistant_messages_path yields no_sentinel gracefully."""

    missing_path = tmp_path / "does_not_exist.jsonl"

    result = parse_l3_result_block(
        stdout="no sentinel here",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=missing_path,
    )

    assert result.outcome == "no_sentinel"


def test_channel_b_jsonl_empty_file(tmp_path) -> None:
    """Empty assistant_messages_path file yields no_sentinel."""

    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("", encoding="utf-8")

    result = parse_l3_result_block(
        stdout="no sentinel here",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=empty_path,
    )

    assert result.outcome == "no_sentinel"


def test_channel_b_jsonl_no_assistant_records(tmp_path) -> None:
    """JSONL with only system/result records (no assistant) yields no_sentinel."""

    path = tmp_path / "session.jsonl"
    records = [
        {"type": "system", "message": "some system message"},
        {"type": "result", "subtype": "success", "result": "done"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    result = parse_l3_result_block(
        stdout="no sentinel here",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=path,
    )

    assert result.outcome == "no_sentinel"


def test_extract_text_from_jsonl_excludes_subagent(tmp_path: Path) -> None:
    """Subagent text blocks must not be extracted as parent session text."""
    from autoskillit.fleet.result_parser import _extract_text_from_jsonl

    parent_line = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "parent result"}]}}
    )
    subagent_line = json.dumps(
        {
            "type": "assistant",
            "subagent_type": "Explore",
            "message": {"content": [{"type": "text", "text": "subagent text"}]},
        }
    )
    f = tmp_path / "test.jsonl"
    f.write_text(f"{parent_line}\n{subagent_line}\n")
    result = _extract_text_from_jsonl(f)
    assert "parent result" in result
    assert "subagent text" not in result


def test_extract_text_from_jsonl_ignores_thinking_blocks(tmp_path: Path) -> None:
    """Thinking blocks do not contribute to sentinel search text."""
    from autoskillit.fleet.result_parser import _extract_text_from_jsonl

    path = tmp_path / "session.jsonl"
    records = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "Internal reasoning only."}]},
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    text = _extract_text_from_jsonl(path)
    assert text == ""


def test_extract_text_from_jsonl_extracts_text_blocks_only(tmp_path: Path) -> None:
    """Only text blocks are returned; thinking blocks are excluded."""
    from autoskillit.fleet.result_parser import _extract_text_from_jsonl

    path = tmp_path / "session.jsonl"
    records = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Private reasoning."},
                    {"type": "text", "text": "Final result here."},
                ]
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    text = _extract_text_from_jsonl(path)
    assert text == "Final result here."
    assert "Private reasoning" not in text


# ---------------------------------------------------------------------------
# Tests for dispatch identity continuity on resume
# ---------------------------------------------------------------------------


def test_parse_accepts_prior_dispatch_id_on_resume() -> None:
    """Prior dispatch_id sentinel should be found when prior_dispatch_ids is provided."""
    original_id = "aaaa1111-bbbb-cccc-dddd-eeee2222ffff"
    new_id = "xxxx9999-yyyy-zzzz-wwww-vvvv8888uuuu"
    sentinel_text = (
        f"---l3-result::{original_id}---\n"
        f'{{"success": true, "reason": "completed", "summary": "done"}}\n'
        f"---end-l3-result::{original_id}---\n"
    )
    result = parse_l3_result_block(
        stdout=sentinel_text,
        expected_dispatch_id=new_id,
        prior_dispatch_ids=[original_id],
    )
    assert result.outcome == "completed_clean"


def test_parse_prefers_primary_dispatch_id_over_prior() -> None:
    """When both primary and prior sentinels exist, primary wins."""
    primary_id = "pppp1111-pppp-pppp-pppp-pppp1111pppp"
    prior_id = "qqqq2222-qqqq-qqqq-qqqq-qqqq2222qqqq"
    sentinel_text = (
        f"---l3-result::{prior_id}---\n"
        f'{{"success": false, "reason": "failed", "summary": "prior"}}\n'
        f"---end-l3-result::{prior_id}---\n"
        f"---l3-result::{primary_id}---\n"
        f'{{"success": true, "reason": "completed", "summary": "current"}}\n'
        f"---end-l3-result::{primary_id}---\n"
    )
    result = parse_l3_result_block(
        stdout=sentinel_text,
        expected_dispatch_id=primary_id,
        prior_dispatch_ids=[prior_id],
    )
    assert result.outcome == "completed_clean"
    assert result.payload["success"] is True
    assert result.payload["summary"] == "current"


def test_parse_without_prior_ids_backward_compatible() -> None:
    """Omitting prior_dispatch_ids should not change existing behavior."""
    dispatch_id = "aaaa1111-bbbb-cccc-dddd-eeee2222ffff"
    wrong_id = "xxxx9999-yyyy-zzzz-wwww-vvvv8888uuuu"
    sentinel_text = (
        f"---l3-result::{dispatch_id}---\n"
        f'{{"success": true, "reason": "completed", "summary": "done"}}\n'
        f"---end-l3-result::{dispatch_id}---\n"
    )
    result = parse_l3_result_block(
        stdout=sentinel_text,
        expected_dispatch_id=wrong_id,
    )
    assert result.outcome == "no_sentinel"


def test_parse_prior_dispatch_ids_jsonl_fallback(tmp_path: Path) -> None:
    """Prior dispatch_id fallback should also work via JSONL when stdout has no match."""
    original_id = "aaaa1111-bbbb-cccc-dddd-eeee2222ffff"
    new_id = "xxxx9999-yyyy-zzzz-wwww-vvvv8888uuuu"
    # stdout has nothing useful; JSONL has the original sentinel
    jsonl_path = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{original_id}---\n"
            f'{{"success": true, "reason": "completed", "summary": "from_jsonl"}}\n'
            f"---end-l3-result::{original_id}---"
        ],
    )
    result = parse_l3_result_block(
        stdout="no sentinel in stdout",
        expected_dispatch_id=new_id,
        assistant_messages_path=jsonl_path,
        prior_dispatch_ids=[original_id],
    )
    assert result.outcome == "completed_clean"
    assert result.payload["summary"] == "from_jsonl"


def test_parse_recovers_sentinel_from_additional_jsonl_paths(tmp_path: Path) -> None:
    """Sentinel in additional_jsonl_paths (prior session) is recovered on primary miss."""
    original_session_jsonl = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{DISPATCH_ID}---\n"
            f'{{"success": true, "summary": "from_original_session"}}\n'
            f"---end-l3-result::{DISPATCH_ID}---"
        ],
        "original_session.jsonl",
    )
    result = parse_l3_result_block(
        stdout="no sentinel here",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=tmp_path / "empty_session.jsonl",
        additional_jsonl_paths=[original_session_jsonl],
    )
    assert result.outcome == "completed_clean"
    assert result.payload["summary"] == "from_original_session"
    assert result.source == "additional_jsonl"


def test_parse_additional_jsonl_paths_scanned_oldest_first(tmp_path: Path) -> None:
    """Additional paths are scanned in order; _scan_for_sentinel returns last sentinel."""
    older = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{DISPATCH_ID}---\n"
            f'{{"summary": "older"}}\n'
            f"---end-l3-result::{DISPATCH_ID}---"
        ],
        "older.jsonl",
    )
    newer = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{DISPATCH_ID}---\n"
            f'{{"summary": "newer"}}\n'
            f"---end-l3-result::{DISPATCH_ID}---"
        ],
        "newer.jsonl",
    )
    result = parse_l3_result_block(
        stdout="",
        expected_dispatch_id=DISPATCH_ID,
        additional_jsonl_paths=[older, newer],
    )
    # _scan_for_sentinel uses rfind, returning the LAST sentinel in each file.
    # Files are scanned in order; first match wins (iteration order).
    # older.jsonl is scanned first and returns "older".
    assert result.outcome == "completed_clean"
    assert result.payload["summary"] == "older"


def test_parse_primary_jsonl_wins_over_additional(tmp_path: Path) -> None:
    """When primary JSONL has the sentinel, additional paths are not consulted."""
    primary_jsonl = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{DISPATCH_ID}---\n"
            f'{{"summary": "primary"}}\n'
            f"---end-l3-result::{DISPATCH_ID}---"
        ],
        "primary.jsonl",
    )
    additional_jsonl = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{DISPATCH_ID}---\n"
            f'{{"summary": "additional"}}\n'
            f"---end-l3-result::{DISPATCH_ID}---"
        ],
        "additional.jsonl",
    )
    result = parse_l3_result_block(
        stdout="",
        expected_dispatch_id=DISPATCH_ID,
        assistant_messages_path=primary_jsonl,
        additional_jsonl_paths=[additional_jsonl],
    )
    assert result.outcome == "completed_clean"
    assert result.payload["summary"] == "primary"


def test_parse_additional_jsonl_paths_nonexistent_skipped(tmp_path: Path) -> None:
    """Non-existent additional paths are skipped without error."""
    existing_jsonl = make_jsonl_file(
        tmp_path,
        [
            f"---l3-result::{DISPATCH_ID}---\n"
            f'{{"summary": "existing"}}\n'
            f"---end-l3-result::{DISPATCH_ID}---"
        ],
        "existing.jsonl",
    )
    result = parse_l3_result_block(
        stdout="",
        expected_dispatch_id=DISPATCH_ID,
        additional_jsonl_paths=[
            tmp_path / "does_not_exist.jsonl",
            existing_jsonl,
        ],
    )
    assert result.outcome == "completed_clean"
    assert result.payload["summary"] == "existing"


def test_ansi_only_stdout_no_sentinel() -> None:
    """Pure ANSI TUI cleanup bytes produce outcome='no_sentinel', no crash."""
    ANSI_TUI_CLEANUP = (
        "\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l"
        "\x1b[>4m\x1b[<u\x1b[?1004l\x1b[?2031l\x1b[?2004l"
        "\x1b[?25h\x1b7\x1b[r\x1b8\x1b]0;\x07\x1b[?25h"
    )
    result = parse_l3_result_block(ANSI_TUI_CLEANUP, DISPATCH_ID)
    assert result.outcome == "no_sentinel"


# ---------------------------------------------------------------------------
# HR-split sentinel immunity tests
# ---------------------------------------------------------------------------


def test_hr_split_open_sentinel_recovered() -> None:
    """HR-split open sentinel (--- on own line before dispatch_id---) is recovered."""
    payload = {"status": "ok"}
    stdout = f"output text\n---\nl3-result::{DISPATCH_ID}---\n{json.dumps(payload)}\n{_close()}"
    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)
    assert result.outcome == "completed_clean"
    assert result.payload == payload


def test_clean_sentinel_regression_guard() -> None:
    """Clean (non-split) sentinels continue to work after normalization is applied."""
    payload = {"status": "ok"}
    stdout = f"output\n{_open()}\n{json.dumps(payload)}\n{_close()}\n"
    result = parse_l3_result_block(stdout=stdout, expected_dispatch_id=DISPATCH_ID)
    assert result.outcome == "completed_clean"
    assert result.payload == payload


# ---------------------------------------------------------------------------
# Resume-scoped JSONL extraction tests
# ---------------------------------------------------------------------------


def test_extract_text_from_jsonl_skip_lines(tmp_path: Path) -> None:
    """_extract_text_from_jsonl with skip_lines returns only text from lines N+ onward."""
    from autoskillit.fleet.result_parser import _extract_text_from_jsonl

    messages_run1 = [f"RUN_1_MARKER message {i}" for i in range(5)]
    messages_run2 = [f"RUN_2_MARKER message {i}" for i in range(5)]
    jsonl_path = make_jsonl_file(tmp_path, messages_run1 + messages_run2)

    text = _extract_text_from_jsonl(jsonl_path, skip_lines=5)
    assert "RUN_2_MARKER" in text
    assert "RUN_1_MARKER" not in text


def test_stale_prior_id_sentinel_returns_no_sentinel_with_offset(tmp_path: Path) -> None:
    """Shared JSONL with prior-run failure sentinel returns no_sentinel when offset skips it."""
    prior_id = "aaaa1111-bbbb-cccc-dddd-eeee2222ffff"
    new_id = "xxxx9999-yyyy-zzzz-wwww-vvvv8888uuuu"

    prior_run_messages = [
        "Some prior run output",
        f"---l3-result::{prior_id}---\n"
        f'{{"success": false, "reason": "context_exhausted"}}\n'
        f"---end-l3-result::{prior_id}---",
    ]
    # Lines 0-9 are prior run (10 JSONL lines with make_jsonl_file producing 1 line per message)
    # Actually make_jsonl_file creates exactly len(messages) lines
    resumed_run_messages = [
        "Resumed run output line 1",
        "Resumed run output line 2",
        "Resumed run output line 3",
    ]
    all_messages = prior_run_messages + resumed_run_messages
    jsonl_path = make_jsonl_file(tmp_path, all_messages)

    result = parse_l3_result_block(
        stdout="",
        expected_dispatch_id=new_id,
        assistant_messages_path=jsonl_path,
        prior_dispatch_ids=[prior_id],
        resume_line_offset=len(prior_run_messages),
    )
    assert result.outcome == "no_sentinel"


def test_prior_id_sentinel_scoped_by_resume_offset(tmp_path: Path) -> None:
    """Stage 3b only searches JSONL content after the resume offset."""
    prior_id = "aaaa1111-bbbb-cccc-dddd-eeee2222ffff"
    new_id = "xxxx9999-yyyy-zzzz-wwww-vvvv8888uuuu"

    # 5 lines with prior-ID failure sentinel
    prior_lines = [
        "Prior run message 1",
        "Prior run message 2",
        f"---l3-result::{prior_id}---\n"
        f'{{"success": false, "reason": "context_exhausted"}}\n'
        f"---end-l3-result::{prior_id}---",
        "Prior run message 4",
        "Prior run message 5",
    ]
    # 5 lines with resumed-run content, no sentinel
    resumed_lines = [
        "Resumed message 1",
        "Resumed message 2",
        "Resumed message 3",
        "Resumed message 4",
        "Resumed message 5",
    ]
    jsonl_path = make_jsonl_file(tmp_path, prior_lines + resumed_lines)

    result = parse_l3_result_block(
        stdout="",
        expected_dispatch_id=new_id,
        assistant_messages_path=jsonl_path,
        prior_dispatch_ids=[prior_id],
        resume_line_offset=len(prior_lines),
    )
    assert result.outcome == "no_sentinel"


def test_resume_line_offset_zero_preserves_existing_behavior(tmp_path: Path) -> None:
    """resume_line_offset=0 reads entire JSONL; prior-ID accepted via prior_dispatch_ids."""
    prior_id = "aaaa1111-bbbb-cccc-dddd-eeee2222ffff"
    new_id = "xxxx9999-yyyy-zzzz-wwww-vvvv8888uuuu"

    messages = [
        f"---l3-result::{prior_id}---\n"
        f'{{"success": true, "reason": "completed"}}\n'
        f"---end-l3-result::{prior_id}---",
    ]
    jsonl_path = make_jsonl_file(tmp_path, messages)

    result = parse_l3_result_block(
        stdout="",
        expected_dispatch_id=new_id,
        assistant_messages_path=jsonl_path,
        prior_dispatch_ids=[prior_id],
        resume_line_offset=0,
    )
    # Without offset, the prior-ID sentinel IS found (existing behavior)
    assert result.outcome == "completed_clean"
