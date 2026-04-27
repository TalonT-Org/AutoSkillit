"""Tests for headless_runner.py extracted helpers."""

import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless import _build_skill_result, _scan_jsonl_write_paths
from tests.execution.test_headless_core import (
    _make_tool_use_line,
    _sr,
    _success_session_json,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Test: _extract_output_paths (Step 1b)
# ---------------------------------------------------------------------------


class TestExtractOutputPaths:
    def test_extracts_single_path(self):
        from autoskillit.execution.headless import _extract_output_paths

        msgs = ["plan_path = /correct/path/.autoskillit/temp/make-plan/foo.md"]
        result = _extract_output_paths(msgs)
        assert result == {"plan_path": "/correct/path/.autoskillit/temp/make-plan/foo.md"}

    def test_extracts_multiple_tokens(self):
        from autoskillit.execution.headless import _extract_output_paths

        msg = (
            "plan_path = /clone/.autoskillit/temp/make-plan/plan.md\n"
            "summary_path = /clone/.autoskillit/temp/report/summary.md\n"
            "investigation_path = /clone/.autoskillit/temp/investigate/inv.md"
        )
        result = _extract_output_paths([msg])
        assert result == {
            "plan_path": "/clone/.autoskillit/temp/make-plan/plan.md",
            "summary_path": "/clone/.autoskillit/temp/report/summary.md",
            "investigation_path": "/clone/.autoskillit/temp/investigate/inv.md",
        }

    def test_returns_empty_when_no_tokens(self):
        from autoskillit.execution.headless import _extract_output_paths

        result = _extract_output_paths(["no tokens here", "just regular text"])
        assert result == {}

    def test_ignores_non_absolute_paths(self):
        from autoskillit.execution.headless import _extract_output_paths

        result = _extract_output_paths(["plan_path = .autoskillit/temp/make-plan/foo.md"])
        assert result == {}

    def test_extracts_from_multiple_messages(self):
        from autoskillit.execution.headless import _extract_output_paths

        msgs = [
            "plan_path = /first/path",
            "investigation_path = /second/path",
        ]
        result = _extract_output_paths(msgs)
        assert result == {
            "plan_path": "/first/path",
            "investigation_path": "/second/path",
        }

    def test_last_occurrence_wins(self):
        from autoskillit.execution.headless import _extract_output_paths

        msgs = [
            "plan_path = /first/path",
            "plan_path = /second/path",
        ]
        result = _extract_output_paths(msgs)
        assert result == {"plan_path": "/second/path"}


# ---------------------------------------------------------------------------
# Test: _validate_output_paths (Step 1c)
# ---------------------------------------------------------------------------


class TestValidateOutputPaths:
    def test_returns_none_when_all_paths_under_cwd(self):
        from autoskillit.execution.headless import _validate_output_paths

        paths = {
            "plan_path": "/clone/path/.autoskillit/temp/make-plan/foo.md",
            "report_path": "/clone/path/.autoskillit/temp/report/bar.md",
        }
        result = _validate_output_paths(paths, "/clone/path")
        assert result is None

    def test_returns_diagnostic_when_path_outside_cwd(self):
        from autoskillit.execution.headless import _validate_output_paths

        paths = {
            "plan_path": "/source/repo/.autoskillit/temp/make-plan/foo.md",
        }
        result = _validate_output_paths(paths, "/clone/path")
        assert result is not None
        assert "plan_path" in result
        assert "/source/repo/.autoskillit/temp/make-plan/foo.md" in result
        assert "/clone/path" in result

    def test_returns_none_for_empty_paths(self):
        from autoskillit.execution.headless import _validate_output_paths

        result = _validate_output_paths({}, "/clone/path")
        assert result is None

    def test_cwd_trailing_slash_handling(self):
        from autoskillit.execution.headless import _validate_output_paths

        paths = {"plan_path": "/clone/path/.autoskillit/temp/foo.md"}
        assert _validate_output_paths(paths, "/clone/path/") is None
        assert _validate_output_paths(paths, "/clone/path") is None

    def test_multiple_violations(self):
        from autoskillit.execution.headless import _validate_output_paths

        paths = {
            "plan_path": "/wrong/.autoskillit/temp/plan.md",
            "report_path": "/wrong/.autoskillit/temp/report.md",
        }
        result = _validate_output_paths(paths, "/clone")
        assert result is not None
        assert "plan_path" in result
        assert "report_path" in result


# ---------------------------------------------------------------------------
# Test: _build_skill_result path contamination detection (Step 1d)
# ---------------------------------------------------------------------------


class TestBuildSkillResultPathContamination:
    @staticmethod
    def _assistant_ndjson(text: str) -> str:
        """Build an NDJSON assistant message line."""
        return json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"text": text}]},
            }
        )

    def test_path_contamination_detected(self):
        """Output paths outside cwd override success to False."""
        path = "/wrong/source/repo/.autoskillit/temp/make-plan/foo.md"
        stdout = (
            self._assistant_ndjson(f"plan_path = {path}")
            + "\n"
            + _success_session_json("Plan created.")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/correct/clone")
        assert sr.success is False
        assert sr.subtype == "path_contamination"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.PATH_CONTAMINATION

    def test_path_contamination_emits_path_contamination_reason(self):
        """Path contamination must emit PATH_CONTAMINATION, not RESUME.

        PATH_CONTAMINATION is not a context limit — it is a CWD boundary violation.
        The orchestrator must route to on_failure, not on_context_limit.
        """
        path = "/wrong/source/repo/.autoskillit/temp/make-plan/bar.md"
        stdout = (
            self._assistant_ndjson(f"plan_path = {path}")
            + "\n"
            + _success_session_json("Plan created.")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/correct/clone")
        assert sr.retry_reason == RetryReason.PATH_CONTAMINATION
        assert sr.needs_retry is True
        # Confirm it is NOT RESUME — routing must not go to on_context_limit

    def test_no_contamination_when_paths_under_cwd(self):
        """All output paths under cwd yields normal result."""
        path = "/correct/clone/.autoskillit/temp/make-plan/foo.md"
        stdout = (
            self._assistant_ndjson(f"plan_path = {path}")
            + "\n"
            + _success_session_json("Plan created.")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/correct/clone")
        assert sr.success is True
        assert sr.subtype != "path_contamination"

    def test_no_contamination_when_cwd_empty(self):
        """Empty cwd skips path validation — direct callers without clone context."""
        path = "/any/path/.autoskillit/temp/foo.md"
        stdout = (
            self._assistant_ndjson(f"plan_path = {path}") + "\n" + _success_session_json("Done.")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="")
        assert sr.success is True

    def test_no_contamination_when_no_path_tokens(self):
        """No output path tokens means validation passes."""
        stdout = _success_session_json("Done with no file output.")
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/clone/path")
        assert sr.success is True


# ---------------------------------------------------------------------------
# Test: run_headless_core passes cwd (Step 1f)
# ---------------------------------------------------------------------------


class TestRunHeadlessCorePassesCwd:
    @pytest.mark.anyio
    async def test_cwd_anchor_injected_into_prompt(self, tool_ctx):
        """Verify run_headless_core injects cwd anchor into the skill prompt."""
        from autoskillit.execution.headless import run_headless_core

        payload = _success_session_json("Result text %%ORDER_UP%%")
        tool_ctx.runner.push(
            SubprocessResult(0, payload, "", TerminationReason.NATURAL_EXIT, pid=1)
        )
        await run_headless_core(
            "/autoskillit:investigate test",
            "/some/test/cwd",
            tool_ctx,
        )
        assert tool_ctx.runner.call_args_list, "Runner was never called"
        last_cmd = tool_ctx.runner.call_args_list[-1][0]
        # cmd is ["env", ...vars, "claude", "-p", <prompt>, ...]
        p_idx = last_cmd.index("-p")
        prompt_arg = last_cmd[p_idx + 1]
        assert "WORKING DIRECTORY ANCHOR" in prompt_arg
        assert "/some/test/cwd" in prompt_arg


class TestScanJsonlWritePaths:
    CWD = "/clone/worktree"

    def test_returns_empty_for_clean_write_inside_cwd(self):
        line = _make_tool_use_line(
            "Write", {"file_path": f"{self.CWD}/.autoskillit/temp/out.md", "content": "x"}
        )
        assert _scan_jsonl_write_paths(line, self.CWD) == []

    def test_detects_write_outside_cwd(self):
        line = _make_tool_use_line(
            "Write", {"file_path": "/source/repo/.autoskillit/temp/stolen.md", "content": "x"}
        )
        warnings = _scan_jsonl_write_paths(line, self.CWD)
        assert len(warnings) == 1
        assert "/source/repo/.autoskillit/temp/stolen.md" in warnings[0]

    def test_detects_edit_outside_cwd(self):
        line = _make_tool_use_line(
            "Edit",
            {
                "file_path": "/source/repo/src/autoskillit/file.py",
                "old_string": "a",
                "new_string": "b",
            },
        )
        warnings = _scan_jsonl_write_paths(line, self.CWD)
        assert len(warnings) == 1
        assert "Edit" in warnings[0]

    def test_detects_bash_with_absolute_path_outside_cwd(self):
        line = _make_tool_use_line(
            "Bash", {"command": "cat /source/repo/README.md > /tmp/out.txt"}
        )
        warnings = _scan_jsonl_write_paths(line, self.CWD)
        assert len(warnings) >= 1
        assert any("/source/repo" in w for w in warnings)

    def test_no_warnings_for_empty_stdout(self):
        assert _scan_jsonl_write_paths("", self.CWD) == []

    def test_no_warnings_for_malformed_jsonl(self):
        assert _scan_jsonl_write_paths("not json at all\n{broken", self.CWD) == []

    def test_no_warnings_for_read_only_tool_calls(self):
        line = _make_tool_use_line("Read", {"file_path": "/source/repo/some_file.py"})
        assert _scan_jsonl_write_paths(line, self.CWD) == []

    def test_multiple_violations_in_one_session(self):
        lines = "\n".join(
            [
                _make_tool_use_line("Write", {"file_path": "/source/repo/a.md", "content": "x"}),
                _make_tool_use_line(
                    "Edit",
                    {"file_path": "/source/repo/b.py", "old_string": "a", "new_string": "b"},
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "done",
                        "session_id": "",
                        "is_error": False,
                    }
                ),
            ]
        )
        warnings = _scan_jsonl_write_paths(lines, self.CWD)
        assert len(warnings) == 2

    def test_no_warning_when_cwd_empty(self):
        line = _make_tool_use_line("Write", {"file_path": "/any/path/file.md", "content": "x"})
        assert _scan_jsonl_write_paths(line, "") == []

    def test_no_warning_when_cwd_is_relative(self):
        line = _make_tool_use_line("Write", {"file_path": "/any/path/file.md", "content": "x"})
        assert _scan_jsonl_write_paths(line, "relative/path") == []


class TestBuildSkillResultWritePathWarnings:
    def test_write_path_warnings_empty_for_clean_session(self):
        stdout = (
            _make_tool_use_line(
                "Write", {"file_path": "/clone/worktree/.autoskillit/temp/out.md", "content": "x"}
            )
            + "\n"
            + _success_session_json("Done %%DONE%%")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/clone/worktree")
        assert sr.write_path_warnings == []

    def test_write_path_warnings_populated_for_contaminated_session(self):
        stdout = (
            _make_tool_use_line(
                "Write", {"file_path": "/source/repo/.autoskillit/temp/stolen.md", "content": "x"}
            )
            + "\n"
            + _success_session_json("Done %%DONE%%")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/clone/worktree")
        assert len(sr.write_path_warnings) == 1
        assert "/source/repo/.autoskillit/temp/stolen.md" in sr.write_path_warnings[0]

    def test_write_path_warnings_appear_in_to_json(self):
        stdout = (
            _make_tool_use_line("Write", {"file_path": "/source/repo/bad.md", "content": "x"})
            + "\n"
            + _success_session_json("Done %%DONE%%")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/clone/worktree")
        data = json.loads(sr.to_json())
        assert "write_path_warnings" in data
        assert len(data["write_path_warnings"]) == 1

    def test_write_path_warnings_independent_of_output_token_contamination(self):
        """Warnings are populated even when _validate_output_paths also fires."""
        # plan_path token must appear in an assistant text message for
        # _validate_output_paths to detect it (it scans assistant_messages,
        # not the final result record).
        path_token_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "plan_path = /source/repo/.autoskillit/temp/plan.md",
                        }
                    ]
                },
            }
        )
        stdout = (
            _make_tool_use_line("Write", {"file_path": "/source/repo/bad.md", "content": "x"})
            + "\n"
            + path_token_line
            + "\n"
            + _success_session_json("Done %%DONE%%")
        )
        result = _sr(0, stdout, "", TerminationReason.NATURAL_EXIT)
        sr = _build_skill_result(result, cwd="/clone/worktree")
        # subtype is path_contamination (from _validate_output_paths)
        assert sr.subtype == "path_contamination"
        # write_path_warnings also populated from JSONL scan
        assert len(sr.write_path_warnings) >= 1


class TestBuildSkillResultChannelBPatternRecovery:
    """When Channel B wins and pattern is absent from result but present in
    assistant_messages, _build_skill_result should recover and produce success=True.
    """

    def test_build_skill_result_channel_b_recovers_pattern_from_assistant_messages(
        self,
    ) -> None:
        """Channel B wins before stdout drain; pattern found in assistant_messages
        → recovery produces success=True with block in result.
        """
        block = "---prepare-issue-result---\n{}\n---/prepare-issue-result---"
        # Assistant message has the block but NOT a standalone %%ORDER_UP%% marker,
        # so _recover_from_separate_marker will not activate. The new
        # _recover_block_from_assistant_messages must find the block here instead.
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": block}],
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",  # stdout not yet drained — Channel B won the race
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = assistant_line + "\n" + result_line

        sub_result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.COMPLETED,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        sr = _build_skill_result(
            sub_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=["---prepare-issue-result---"],
        )
        assert sr.success is True
        assert "---prepare-issue-result---" in sr.result

    def test_synthesis_not_run_for_unparseable_channel_b_with_write_evidence(self) -> None:
        """CHANNEL_B + UNPARSEABLE + write evidence must not produce success=True.

        Recovery (and CHANNEL_B bypass) must be blocked when session.session_complete
        is False. Synthesis must not inject the file path into result.
        """

        tool_use_line = json.dumps(
            {
                "type": "tool_use",
                "id": "t1",
                "name": "Write",
                "input": {
                    "file_path": "/cwd/.autoskillit/temp/make-plan/arch_lens_selection_2026-01-01.md"  # noqa: E501
                },
            }
        )
        # UNPARSEABLE result (no proper result record)
        raw_stdout = tool_use_line + "\ngarbage ndjson partial"

        sub_result = SubprocessResult(
            returncode=1,
            stdout=raw_stdout,
            stderr="",
            termination=TerminationReason.COMPLETED,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        sr = _build_skill_result(
            sub_result,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert sr.success is False
        assert sr.subtype not in {"success"}
        # Synthesis must not have injected the structured token (raw NDJSON may contain
        # the file path string, but the plan_path = ... assignment must be absent)
        assert "plan_path =" not in sr.result

    def test_synthesis_not_run_for_timeout_channel_b_with_write_evidence(self) -> None:
        """CHANNEL_B + TIMEOUT + write evidence must not produce success=True."""
        tool_use_line = json.dumps(
            {
                "type": "tool_use",
                "id": "t1",
                "name": "Write",
                "input": {
                    "file_path": "/cwd/.autoskillit/temp/make-plan/arch_lens_selection_2026-01-01.md"  # noqa: E501
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = tool_use_line + "\n" + result_line

        sub_result = SubprocessResult(
            returncode=-1,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        sr = _build_skill_result(
            sub_result,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert sr.success is False
        assert "arch_lens_selection" not in sr.result

    def test_synthesis_skipped_for_channel_b_session_complete(self) -> None:
        """CHANNEL_B + SUCCESS + write evidence but no pattern in assistant_messages.

        Synthesis must NOT fabricate the token — if pattern is absent from
        assistant_messages, the agent never emitted it.
        """
        tool_use_line = json.dumps(
            {
                "type": "tool_use",
                "id": "t1",
                "name": "Write",
                "input": {"file_path": "/cwd/.autoskillit/temp/make-plan/task_plan.md"},
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = tool_use_line + "\n" + result_line

        sub_result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.COMPLETED,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        sr = _build_skill_result(
            sub_result,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert sr.success is False
        assert "plan_path" not in sr.result
