"""Contract: sessions expected to write must actually write.

Verifies the behavioral write-count gate that detects silent degradation —
sessions that report success but produced zero Edit/Write tool calls on a
skill classified as write-expected.
"""

from __future__ import annotations

import json

from autoskillit.core import RetryReason
from autoskillit.execution.headless import _build_skill_result, extract_skill_name
from tests.conftest import _make_result


def _ndjson_with_tool_uses(tool_names: list[str]) -> str:
    """Build NDJSON stdout with assistant tool_use blocks and a success result."""
    lines: list[str] = []
    content_blocks = [
        {"type": "tool_use", "name": name, "id": f"tu_{i}"} for i, name in enumerate(tool_names)
    ]
    if content_blocks:
        assistant = {
            "type": "assistant",
            "message": {"content": content_blocks},
        }
        lines.append(json.dumps(assistant))
    result_record = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "session_id": "test-sess",
    }
    lines.append(json.dumps(result_record))
    return "\n".join(lines)


class TestZeroWriteDetection:
    """Zero-write gate: write-expected skills must produce writes."""

    def test_zero_writes_on_write_expected_skill_fails(self) -> None:
        stdout = _ndjson_with_tool_uses(["Read", "Grep"])  # no Edit/Write
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/dry-walkthrough temp/plan.md",
        )
        assert not sr.success
        assert sr.subtype == "zero_writes"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.ZERO_WRITES

    def test_nonzero_writes_on_write_expected_skill_succeeds(self) -> None:
        stdout = _ndjson_with_tool_uses(["Read", "Edit", "Write"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/dry-walkthrough temp/plan.md",
        )
        assert sr.success is True
        assert sr.subtype != "zero_writes"

    def test_zero_writes_on_read_only_skill_succeeds(self) -> None:
        stdout = _ndjson_with_tool_uses(["Read", "Grep"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/investigate error in module",
        )
        assert sr.success is True
        assert sr.subtype != "zero_writes"

    def test_zero_writes_with_no_tool_uses_on_write_expected_fails(self) -> None:
        stdout = _ndjson_with_tool_uses([])  # no tool uses at all
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/make-plan task description",
        )
        assert not sr.success
        assert sr.subtype == "zero_writes"
        assert sr.retry_reason == RetryReason.ZERO_WRITES


class TestWriteCallCountPropagation:
    """write_call_count must be accurately computed and propagated."""

    def test_write_count_counts_edit_and_write(self) -> None:
        stdout = _ndjson_with_tool_uses(["Edit", "Write", "Edit", "Read", "Write"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/investigate something",
        )
        assert sr.write_call_count == 4

    def test_write_count_zero_when_no_writes(self) -> None:
        stdout = _ndjson_with_tool_uses(["Read", "Grep", "Glob"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/investigate something",
        )
        assert sr.write_call_count == 0

    def test_write_count_in_json_output(self) -> None:
        stdout = _ndjson_with_tool_uses(["Edit", "Write"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/investigate something",
        )
        parsed = json.loads(sr.to_json())
        assert parsed["write_call_count"] == 2


class TestExtractSkillName:
    """extract_skill_name handles both namespace forms."""

    def test_autoskillit_namespace(self) -> None:
        assert extract_skill_name("/autoskillit:dry-walkthrough arg") == "dry-walkthrough"

    def test_bare_namespace(self) -> None:
        assert extract_skill_name("/make-plan arg1 arg2") == "make-plan"

    def test_no_slash_returns_none(self) -> None:
        assert extract_skill_name("Fix the bug") is None

    def test_leading_whitespace(self) -> None:
        assert extract_skill_name("  /investigate error") == "investigate"
