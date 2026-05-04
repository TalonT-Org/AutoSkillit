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


def make_jsonl_file(tmp_path, messages: list[str]) -> Path:
    """Write a JSONL file with type=assistant records containing given text."""
    path: Path = tmp_path / "session.jsonl"
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
