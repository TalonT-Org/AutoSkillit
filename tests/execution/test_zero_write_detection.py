"""Contract: sessions expected to write must actually write.

Verifies the behavioral write-count gate that detects silent degradation —
sessions that report success but produced zero Edit/Write tool calls on a
skill classified as write-expected via WriteBehaviorSpec.
"""

from __future__ import annotations

import json

from autoskillit.core import RetryReason, WriteBehaviorSpec, extract_skill_name
from autoskillit.execution.headless import _build_skill_result
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

    def test_always_write_zero_writes_fails(self) -> None:
        stdout = _ndjson_with_tool_uses(["Read", "Grep"])  # no Edit/Write
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/make-plan task",
            write_behavior=WriteBehaviorSpec(mode="always"),
        )
        assert not sr.success
        assert sr.subtype == "zero_writes"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.ZERO_WRITES

    def test_always_write_nonzero_writes_passes(self) -> None:
        stdout = _ndjson_with_tool_uses(["Read", "Edit", "Write"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/make-plan task",
            write_behavior=WriteBehaviorSpec(mode="always"),
        )
        assert sr.success is True
        assert sr.subtype != "zero_writes"

    def test_conditional_write_pattern_absent_passes(self) -> None:
        """Conditional skill, zero writes, pattern NOT in output → writes not expected → pass."""
        stdout = _ndjson_with_tool_uses(["Read", "Grep"])
        # Inject output that does NOT contain conflict_report_path
        result_record = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "worktree_path = /tmp/wt",
            "session_id": "test-sess",
        }
        stdout = (
            _ndjson_with_tool_uses(["Read"]).rsplit("\n", 1)[0] + "\n" + json.dumps(result_record)
        )
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/resolve-merge-conflicts",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"conflict_report_path\s*=\s*/.+",),
            ),
        )
        assert sr.success is True

    def test_conditional_write_pattern_present_zero_writes_fails(self) -> None:
        """Conditional skill, zero writes, pattern IN output → writes expected → fail."""
        result_record = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "conflict_report_path = /tmp/wt/.autoskillit/temp/report.md\nworktree_path = /tmp/wt",  # noqa: E501
            "session_id": "test-sess",
        }
        stdout = json.dumps(result_record)
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/resolve-merge-conflicts",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"conflict_report_path\s*=\s*/.+",),
            ),
        )
        assert not sr.success
        assert sr.subtype == "zero_writes"

    def test_no_write_behavior_passes(self) -> None:
        """WriteBehaviorSpec with mode=None → gate inactive → pass."""
        stdout = _ndjson_with_tool_uses(["Read", "Grep"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/investigate err",
            write_behavior=WriteBehaviorSpec(),
        )
        assert sr.success is True

    def test_none_write_behavior_param_passes(self) -> None:
        """write_behavior=None → backward compatible, no gate → pass."""
        stdout = _ndjson_with_tool_uses(["Read", "Grep"])
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/investigate err",
            write_behavior=None,
        )
        assert sr.success is True

    def test_zero_writes_with_no_tool_uses_on_always_write_fails(self) -> None:
        stdout = _ndjson_with_tool_uses([])  # no tool uses at all
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/make-plan task description",
            write_behavior=WriteBehaviorSpec(mode="always"),
        )
        assert not sr.success
        assert sr.subtype == "zero_writes"
        assert sr.retry_reason == RetryReason.ZERO_WRITES

    def test_conditional_already_green_worktree_not_demoted(self) -> None:
        """resolve-failures conditional + 'fixes_applied = 0' output → success preserved.

        This is the Issue #603 false-positive scenario: worktree is already green,
        skill emits 'fixes_applied = 0', and the gate must NOT demote to zero_writes.
        """
        result_record = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Tests are green. fixes_applied = 0\nno changes needed\n%%ORDER_UP%%",
            "session_id": "test-sess",
        }
        stdout = json.dumps(result_record)
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-failures /tmp/wt /tmp/plan.md main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"fixes_applied\s*=\s*[1-9][0-9]*",),
            ),
        )
        assert sr.success is True, (
            "Already-green worktree (fixes_applied = 0) must NOT be demoted to zero_writes. "
            "The pattern [1-9][0-9]* must not match '0'."
        )
        assert sr.subtype != "zero_writes"

    def test_conditional_fix_applied_but_no_writes_demoted(self) -> None:
        """Conditional + 'fixes_applied = 1' + 0 writes → zero_writes (Bash-only fix, no artifact).

        If the skill claims it applied fixes but produced no Edit/Write calls,
        the gate must fire. This catches the Bash-only fix scenario where the
        skill forgot to write the fix_log artifact.
        """
        result_record = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "fixes_applied = 1\nfixed: uv.lock stale pin\n%%ORDER_UP%%",
            "session_id": "test-sess",
        }
        stdout = json.dumps(result_record)
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-failures /tmp/wt /tmp/plan.md main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"fixes_applied\s*=\s*[1-9][0-9]*",),
            ),
        )
        assert sr.success is False
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
