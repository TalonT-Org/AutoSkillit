"""Tests for headless.py: _build_skill_result, path validation, synthesis, and contract gates."""

import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless import (
    _build_skill_result,
    _extract_missing_token_hints,
)
from autoskillit.execution.session import ClaudeSessionResult
from autoskillit.pipeline.audit import DefaultAuditLog, FailureRecord
from tests.conftest import _make_result
from tests.execution.conftest import _make_tool_use_line, _success_session_json

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBuildSkillResultChannelAPatternRecovery:
    """_build_skill_result must extend pattern recovery to CHANNEL_A wins, not just CHANNEL_B."""

    def test_build_skill_result_channel_a_recovers_from_assistant_messages(self) -> None:
        """_build_skill_result must attempt pattern recovery for CHANNEL_A, not just CHANNEL_B."""
        block = "verdict = GO\n%%ORDER_UP%%"
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
                "result": "Done. %%ORDER_UP%%",
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = assistant_line + "\n" + result_line
        sub_result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        sr = _build_skill_result(
            sub_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=["verdict\\s*=\\s*(GO|NO GO)"],
        )
        assert sr.success is True, (
            "CHANNEL_A should recover patterns from assistant_messages, same as CHANNEL_B."
        )

    def test_build_skill_result_channel_a_pattern_contract_violation_is_terminal(
        self,
    ) -> None:
        """After failed CHANNEL_A recovery, contract violation must be FAILED, not RETRIABLE."""
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "No verdict here. %%ORDER_UP%%"}],
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done. %%ORDER_UP%%",
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = assistant_line + "\n" + result_line
        sub_result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        sr = _build_skill_result(
            sub_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=["verdict\\s*=\\s*(GO|NO GO)"],
        )
        assert sr.success is False
        assert sr.needs_retry is False
        assert sr.subtype == "adjudicated_failure"

    def test_bold_wrapped_plan_path_returns_success_not_adjudicated_failure(
        self,
    ) -> None:
        """Regression test for issue #462: model emitting **plan_path** = /abs/path
        must produce success=True, not adjudicated_failure."""
        result_text = (
            "The implementation plan has been written.\n\n"
            "**plan_path** = /abs/path/plan.md\n"
            "**plan_parts** = /abs/path/plan.md\n"
            "%%ORDER_UP%%"
        )
        sub_result = SubprocessResult(
            returncode=0,
            stdout=_success_session_json(result_text),
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )
        sr = _build_skill_result(
            sub_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=["plan_path\\s*=\\s*/.+"],
        )
        assert sr.success is True
        assert sr.subtype != "adjudicated_failure"
        assert sr.needs_retry is False

    def test_bold_wrapped_tokens_in_assistant_messages_recovery_succeeds(
        self,
    ) -> None:
        """Recovery path: bold-wrapped tokens in assistant_messages must also be found.
        The pattern_recovery block in _build_skill_result requires channel_confirmation
        != UNMONITORED to trigger; use CHANNEL_A to activate the recovery path."""
        token_block = "**plan_path** = /abs/path/plan.md\n**plan_parts** = /abs/path/plan.md"
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": token_block}],
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "The plan is complete. %%ORDER_UP%%",
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = assistant_line + "\n" + result_line
        sub_result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        sr = _build_skill_result(
            sub_result,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=["plan_path\\s*=\\s*/.+"],
        )
        assert sr.success is True
        assert sr.subtype != "adjudicated_failure"
        assert sr.needs_retry is False


class TestBuildSkillResultDirMissingRecovery:
    """DIR_MISSING channel confirmation does not silently pass — it attempts
    late-bind recovery when conditions allow."""

    def test_dir_missing_with_recoverable_subtype_attempts_recovery(self):
        """When channel_confirmation is DIR_MISSING and subtype is recoverable,
        the recovery gate must attempt marker-based recovery.

        Setup: termination=COMPLETED (bypasses stale branch), subtype=empty_output
        (in _CHANNEL_B_RECOVERABLE_SUBTYPES), marker on a standalone line in the
        assistant message (required by _marker_is_standalone).
        """
        import structlog.testing

        marker = "===DONE==="
        # Marker must appear as a standalone line for _marker_is_standalone to match
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": f"Task complete.\n\n{marker}"}],
                },
            }
        )
        # subtype=empty_output places the session in _CHANNEL_B_RECOVERABLE_SUBTYPES,
        # triggering the DIR_MISSING recovery guard in _build_skill_result
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "is_error": True,
                "result": "",
                "session_id": "s1",
                "errors": [],
            }
        )
        stdout = assistant_line + "\n" + result_line
        # termination=COMPLETED skips the stale-branch early return so execution
        # reaches the Channel B / DIR_MISSING recovery gate
        sub_result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.COMPLETED,
            pid=12345,
            channel_confirmation=ChannelConfirmation.DIR_MISSING,
        )
        with structlog.testing.capture_logs() as logs:
            skill = _build_skill_result(sub_result, completion_marker=marker)
        # Recovery should succeed — marker found as standalone line in assistant_messages
        assert skill.success is True
        # Verify the recovery code path was taken, not just the outcome
        assert any(e.get("event") == "dir_missing_late_bind_recovery" for e in logs)

    def test_dir_missing_without_marker_does_not_recover(self):
        """DIR_MISSING with no completion marker must not silently succeed."""
        sub_result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
            channel_confirmation=ChannelConfirmation.DIR_MISSING,
        )
        skill = _build_skill_result(sub_result)
        assert skill.success is False


class TestTimedOutSessionPreservesState:
    """TIMED_OUT branch must parse stdout to preserve tool_uses and assistant_messages."""

    def test_timed_out_with_writes_preserves_write_call_count(self):
        """Timed-out session with Write/Edit tool_use blocks must report write_call_count > 0."""
        from autoskillit.execution.headless import _build_skill_result

        ndjson = "\n".join(
            [
                _make_tool_use_line("Write", {"file_path": "/tmp/a.py", "content": "x"}),
                _make_tool_use_line(
                    "Edit", {"file_path": "/tmp/b.py", "old_string": "a", "new_string": "b"}
                ),
            ]
        )
        sub_result = SubprocessResult(
            returncode=-1,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=12345,
        )
        sr = _build_skill_result(sub_result)
        assert sr.write_call_count == 2
        assert sr.success is False
        assert sr.needs_retry is False

    def test_timed_out_with_empty_stdout_uses_timeout_subtype(self):
        """Timed-out session with no stdout uses TIMEOUT subtype."""
        from autoskillit.execution.headless import _build_skill_result

        sub_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=12345,
        )
        sr = _build_skill_result(sub_result)
        assert sr.success is False
        assert sr.write_call_count == 0

    def test_timed_out_with_success_result_overrides_to_timeout(self):
        """When timed-out stdout has a success result, subtype is overridden to timeout."""
        from autoskillit.execution.headless import _build_skill_result

        ndjson = "\n".join(
            [
                _make_tool_use_line("Write", {"file_path": "/tmp/a.py", "content": "x"}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Task completed.",
                        "session_id": "s1",
                    }
                ),
            ]
        )
        sub_result = SubprocessResult(
            returncode=-1,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=12345,
        )
        sr = _build_skill_result(sub_result)
        assert sr.cli_subtype == "timeout"
        assert sr.write_call_count == 1


class TestOutputPathTokensDerivedFromContracts:
    # Hardcoded fixture — update when skill_contracts.yaml gains new file_path outputs.
    # Using a fixed set (rather than re-deriving) ensures bugs in the derivation formula
    # cause test failures rather than silent agreement between production and test code.
    _EXPECTED_OUTPUT_PATH_TOKENS = frozenset(
        {
            "analysis_file",
            "campaign_path",
            "conflict_report_path",
            "diagnosis_path",
            "diagram_path",
            "evaluation_dashboard",
            "experiment_plan",
            "group_files",
            "groups_path",
            "investigation_path",
            "manifest_path",
            "plan_parts",
            "plan_path",
            "pr_order_file",
            "prep_path",
            "recipe_path",
            "remediation_path",
            "report_path",
            "report_plan_path",
            "results_path",
            "review_path",
            "revision_guidance",
            "scope_report",
            "summary_path",
            "triage_manifest",
            "triage_report",
            "visualization_plan_path",
            "html_path",
            "resource_report",
            "execution_map",
            "execution_map_report",
            # planner-generate-phases output (planner recipe)
            "phase_manifest_path",
            # planner-elaborate-phase output (parallel worker)
            "elab_result_path",
            # planner-refine-phases output
            "refined_plan_path",
            # planner-refine-assignments output
            "refined_assignments_path",
            # planner-refine-wps output
            "refined_wps_path",
        }
    )

    def test_output_path_tokens_contains_all_file_path_contract_outputs(self) -> None:
        """Every skill output declared with type=file_path or type=file_path_list in
        skill_contracts.yaml must appear in _OUTPUT_PATH_TOKENS (or be documented as
        intentionally excluded)."""
        from autoskillit.execution.headless import (
            _INTENTIONALLY_EXCLUDED_PATH_TOKENS,
            _OUTPUT_PATH_TOKENS,
        )
        from autoskillit.recipe.contracts import load_bundled_manifest

        manifest = load_bundled_manifest()
        declared_path_tokens = {
            out["name"]
            for skill_data in manifest.get("skills", {}).values()
            for out in skill_data.get("outputs", [])
            if isinstance(out, dict) and out.get("type", "").startswith("file_path")
        }
        untracked = (
            declared_path_tokens - _OUTPUT_PATH_TOKENS - _INTENTIONALLY_EXCLUDED_PATH_TOKENS
        )
        assert not untracked, (
            f"These path tokens are declared in skill_contracts.yaml but missing from "
            f"_OUTPUT_PATH_TOKENS or _INTENTIONALLY_EXCLUDED_PATH_TOKENS: {untracked}"
        )

    def test_output_path_tokens_matches_expected_fixture(self) -> None:
        """_OUTPUT_PATH_TOKENS must exactly match the known fixture set.

        This guards against bugs in the derivation formula: if _build_path_token_set()
        is broken, both the production frozenset and a re-derived set would agree, but
        this hardcoded fixture would not.
        """
        from autoskillit.execution.headless import _OUTPUT_PATH_TOKENS

        extra = _OUTPUT_PATH_TOKENS - self._EXPECTED_OUTPUT_PATH_TOKENS
        missing = self._EXPECTED_OUTPUT_PATH_TOKENS - _OUTPUT_PATH_TOKENS
        assert _OUTPUT_PATH_TOKENS == self._EXPECTED_OUTPUT_PATH_TOKENS, (
            f"_OUTPUT_PATH_TOKENS diverged from expected fixture.\n"
            f"Extra (in production, not in fixture): {extra}\n"
            f"Missing (in fixture, not in production): {missing}"
        )


@pytest.fixture
def make_headless_session():
    """Build a ClaudeSessionResult with configurable result and tool_uses (parsed form)."""

    def _factory(
        result: str = "",
        tool_uses: list[dict] | None = None,
        subtype: str = "success",
        is_error: bool = False,
    ) -> ClaudeSessionResult:
        return ClaudeSessionResult(
            subtype=subtype,
            is_error=is_error,
            result=result,
            session_id="test-session",
            tool_uses=tool_uses or [],
        )

    return _factory


class TestSynthesizeFromWriteArtifacts:
    def test_synthesizes_plan_path_from_write_tool_use(self, make_headless_session):
        """When Write file_path is absolute and pattern is plan_path=, token is injected."""
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        session = make_headless_session(
            result="plan summary\n%%ORDER_UP%%",
            tool_uses=[
                {"name": "Write", "id": "t1", "file_path": "/abs/temp/make-plan/my_plan.md"}
            ],
        )
        patterns = [r"plan_path\s*=\s*/.+"]
        result = _synthesize_from_write_artifacts(session, patterns, write_call_count=1)
        assert result is not None
        assert "plan_path = /abs/temp/make-plan/my_plan.md" in result.result

    def test_returns_none_when_no_write_tool_uses(self, make_headless_session):
        """No synthesis when write_call_count == 0."""
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        session = make_headless_session(result="plan summary\n%%ORDER_UP%%", tool_uses=[])
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=0
        )
        assert result is None

    def test_returns_none_when_no_absolute_file_path(self, make_headless_session):
        """Relative file_path in Write tool_use is not used for synthesis."""
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        session = make_headless_session(
            result="plan summary\n%%ORDER_UP%%",
            tool_uses=[{"name": "Write", "id": "t1", "file_path": "temp/make-plan/plan.md"}],
        )
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is None

    def test_returns_none_when_pattern_already_satisfied(self, make_headless_session):
        """No synthesis when the pattern is already in session.result."""
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        session = make_headless_session(
            result="plan_path = /abs/plan.md\n%%ORDER_UP%%",
            tool_uses=[{"name": "Write", "id": "t1", "file_path": "/abs/plan.md"}],
        )
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=1
        )
        assert result is None

    def test_returns_none_when_no_path_capture_patterns(self, make_headless_session):
        """Non-path patterns (verdict= etc) are not attempted for synthesis."""
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        session = make_headless_session(
            result="%%ORDER_UP%%",
            tool_uses=[{"name": "Write", "id": "t1", "file_path": "/abs/plan.md"}],
        )
        result = _synthesize_from_write_artifacts(
            session, [r"verdict\s*=\s*(GO|NO GO)"], write_call_count=1
        )
        assert result is None

    def test_synthesis_uses_last_write_not_first(self, make_headless_session):
        """When multiple Write tool_uses exist, synthesis must use the LAST absolute path.

        Multi-artifact skills write intermediate files first, final deliverable last.
        Synthesis must inject the final deliverable (last path), not the intermediate.
        """
        from autoskillit.execution.headless import _synthesize_from_write_artifacts

        session = make_headless_session(
            result="",
            tool_uses=[
                {
                    "name": "Write",
                    "id": "t1",
                    "file_path": "/cwd/.autoskillit/temp/make-plan/arch_lens_selection.md",
                },
                {
                    "name": "Write",
                    "id": "t2",
                    "file_path": "/cwd/.autoskillit/temp/make-plan/task_plan_2026-01-01.md",
                },
            ],
        )
        result = _synthesize_from_write_artifacts(
            session, [r"plan_path\s*=\s*/.+"], write_call_count=2
        )
        assert result is not None
        assert (
            "plan_path = /cwd/.autoskillit/temp/make-plan/task_plan_2026-01-01.md" in result.result
        )
        assert "arch_lens_selection" not in result.result


@pytest.fixture
def make_build_skill_result_kwargs():
    """Build a dict of kwargs for _build_skill_result from high-level parameters.

    tool_uses entries use the parsed form: 'file_path' at the top level (not nested
    in 'input'). The fixture converts them to raw NDJSON tool_use content blocks
    with 'input': {'file_path': ...} so parse_session_result (after Step 3) preserves them.

    consecutive_failures_over_budget=True creates an audit with 4 recorded failures
    (> max_consecutive_retries=3 default) so the budget guard fires.
    """
    from datetime import UTC, datetime

    def _factory(
        result_text: str = "done",
        completion_marker: str = "",
        expected_output_patterns: list[str] | None = None,
        tool_uses: list[dict] | None = None,
        consecutive_failures_over_budget: bool = False,
    ) -> dict:
        records = []
        if tool_uses:
            content = []
            for tu in tool_uses:
                block: dict = {"type": "tool_use", "name": tu["name"], "id": tu.get("id", "")}
                fp = tu.get("file_path")
                if fp:
                    block["input"] = {"file_path": fp}
                content.append(block)
            records.append(json.dumps({"type": "assistant", "message": {"content": content}}))
        records.append(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": result_text,
                    "session_id": "test-session",
                }
            )
        )
        ndjson = "\n".join(records)
        result = SubprocessResult(
            returncode=0,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )
        audit = None
        skill_command = ""
        if consecutive_failures_over_budget:
            skill_command = "/autoskillit:make-plan"
            audit = DefaultAuditLog()
            for _ in range(4):  # 4 > max_consecutive_retries=3 → budget exhausted
                audit.record_failure(
                    FailureRecord(  # type: ignore[arg-type]
                        timestamp=datetime.now(UTC).isoformat(),
                        skill_command=skill_command,
                        exit_code=-1,
                        subtype="stale",
                        needs_retry=True,
                        retry_reason="stale",
                        stderr="",
                    )
                )
        return {
            "result": result,
            "completion_marker": completion_marker,
            "expected_output_patterns": expected_output_patterns or [],
            "skill_command": skill_command,
            "audit": audit,
        }

    return _factory


class TestContractRecoveryGate:
    def test_adjudicated_failure_with_write_evidence_becomes_retriable(
        self, make_build_skill_result_kwargs
    ):
        """
        COMPLETED + marker present + plan_path absent + Write call with no file_path
        → synthesis finds no usable path → CONTRACT_RECOVERY gate fires
        → needs_retry=True, retry_reason=contract_recovery.
        """
        kwargs = make_build_skill_result_kwargs(
            result_text="plan summary\n%%ORDER_UP%%",
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            tool_uses=[
                # Write call exists (write evidence present) but no file_path key
                # → synthesis has nothing to inject → CONTRACT_RECOVERY gate fires
                {"name": "Write", "id": "t1"}
            ],
        )
        sr = _build_skill_result(**kwargs)
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.CONTRACT_RECOVERY
        assert sr.success is False

    def test_adjudicated_failure_without_write_evidence_stays_terminal(
        self, make_build_skill_result_kwargs
    ):
        """
        COMPLETED + marker present + plan_path absent + write_call_count == 0
        → success=False, needs_retry=False, subtype=adjudicated_failure (unchanged behavior).
        """
        kwargs = make_build_skill_result_kwargs(
            result_text="plan summary\n%%ORDER_UP%%",
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            tool_uses=[],
        )
        sr = _build_skill_result(**kwargs)
        assert sr.success is False
        assert sr.needs_retry is False
        assert sr.subtype == "adjudicated_failure"

    def test_synthesis_succeeds_plan_path_token_from_write_file_path(
        self, make_build_skill_result_kwargs
    ):
        """
        COMPLETED + marker present + plan_path absent + Write file_path is absolute
        → synthesis injects token → success=True (no CONTRACT_RECOVERY needed).
        """
        kwargs = make_build_skill_result_kwargs(
            result_text="plan summary\n%%ORDER_UP%%",
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            tool_uses=[
                {"name": "Write", "id": "t1", "file_path": "/abs/temp/make-plan/my_plan.md"}
            ],
        )
        sr = _build_skill_result(**kwargs)
        assert sr.success is True
        assert "plan_path = /abs/temp/make-plan/my_plan.md" in sr.result

    def test_contract_recovery_respects_budget_guard(self, make_build_skill_result_kwargs):
        """
        When budget is exhausted, budget guard must override CONTRACT_RECOVERY.
        Write call with no file_path → synthesis fails → CONTRACT_RECOVERY promotes
        to retriable → budget guard fires (second call inside gate) → needs_retry=False.
        """
        kwargs = make_build_skill_result_kwargs(
            result_text="plan summary\n%%ORDER_UP%%",
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            tool_uses=[{"name": "Write", "id": "t1"}],  # no file_path → synthesis fails
            consecutive_failures_over_budget=True,
        )
        sr = _build_skill_result(**kwargs)
        assert sr.needs_retry is False
        assert sr.retry_reason == RetryReason.BUDGET_EXHAUSTED


class TestBuildSkillResultSessionIdFromSubprocess:
    """_build_skill_result propagates result.session_id on all paths."""

    def test_stale_path_session_id_from_subprocess_result(self) -> None:
        """Stale path: SkillResult.session_id == result.session_id (not hardcoded '')."""
        result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=1,
            session_id="real-uuid-from-channel-b",
        )
        sr = _build_skill_result(result)
        assert sr.session_id == "real-uuid-from-channel-b"

    def test_timeout_empty_stdout_session_id_from_subprocess_result(self) -> None:
        """TIMED_OUT with empty stdout: SkillResult.session_id == result.session_id."""
        result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.TIMED_OUT,
            pid=1,
            session_id="real-uuid-from-channel-b",
        )
        sr = _build_skill_result(result)
        assert sr.session_id == "real-uuid-from-channel-b"

    def test_channel_a_session_id_takes_precedence(self) -> None:
        """When stdout has a result record with session_id, it wins over result.session_id."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "session_id": "stdout-uuid",
                "is_error": False,
            }
        )
        result = SubprocessResult(
            returncode=0,
            stdout=stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
            session_id="ch-b-uuid",
        )
        sr = _build_skill_result(result)
        assert sr.session_id == "stdout-uuid"

    def test_context_exhaustion_session_id_from_subprocess_result(self) -> None:
        """Context-exhaustion (no result record): uses result.session_id as fallback."""
        partial_assistant_stdout = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Doing work..."}]},
            }
        )
        result = SubprocessResult(
            returncode=1,
            stdout=partial_assistant_stdout,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
            session_id="ch-b-uuid-5678",
        )
        sr = _build_skill_result(result)
        assert sr.session_id == "ch-b-uuid-5678"


class TestHeadlessExecutorCompletionMarker:
    """Protocol conformance: DefaultHeadlessExecutor.run accepts completion_marker."""

    def test_headless_executor_accepts_completion_marker(self) -> None:
        import inspect

        from autoskillit.execution.headless import DefaultHeadlessExecutor

        sig = inspect.signature(DefaultHeadlessExecutor.run)
        assert "completion_marker" in sig.parameters
        param = sig.parameters["completion_marker"]
        assert param.default == ""


class TestHeadlessExecutorIdleOutputTimeout:
    """Protocol conformance and resolution logic for idle_output_timeout."""

    def test_headless_executor_accepts_idle_output_timeout(self) -> None:
        import inspect

        from autoskillit.execution.headless import DefaultHeadlessExecutor

        sig = inspect.signature(DefaultHeadlessExecutor.run)
        assert "idle_output_timeout" in sig.parameters
        param = sig.parameters["idle_output_timeout"]
        assert param.default is None

    def _success_payload(self, marker: str) -> SubprocessResult:
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Done. {marker}",
                "session_id": "sess-iot",
            }
        )
        return SubprocessResult(0, payload, "", TerminationReason.NATURAL_EXIT, pid=1)

    @pytest.mark.anyio
    async def test_default_headless_executor_uses_per_step_idle_output_timeout(
        self, tool_ctx
    ) -> None:
        """idle_output_timeout=120 is converted to float and passed to the runner."""
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._success_payload(marker))
        await run_headless_core(
            "/investigate foo", cwd="/tmp", ctx=tool_ctx, idle_output_timeout=120.0
        )
        _, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
        assert kwargs["idle_output_timeout"] == 120.0

    @pytest.mark.anyio
    async def test_default_headless_executor_converts_zero_to_none(self, tool_ctx) -> None:
        """idle_output_timeout=0 is converted to None (disabled) before passing to runner."""
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._success_payload(marker))
        await run_headless_core(
            "/investigate foo", cwd="/tmp", ctx=tool_ctx, idle_output_timeout=0.0
        )
        _, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
        assert kwargs["idle_output_timeout"] is None

    @pytest.mark.anyio
    async def test_default_headless_executor_falls_back_to_cfg_idle_output_timeout(
        self, tool_ctx
    ) -> None:
        """idle_output_timeout=None falls back to float(cfg.idle_output_timeout)."""
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._success_payload(marker))
        await run_headless_core(
            "/investigate foo", cwd="/tmp", ctx=tool_ctx, idle_output_timeout=None
        )
        _, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
        assert kwargs["idle_output_timeout"] == 600.0


def _ndjson_with_write(result_text: str, file_paths: list[str], session_id: str = "test-session"):
    """Build NDJSON stdout with Write tool_use entries and a result record."""
    records = []
    if file_paths:
        content = [
            {
                "type": "tool_use",
                "name": "Write",
                "id": f"t{i}",
                "input": {"file_path": fp},
            }
            for i, fp in enumerate(file_paths)
        ]
        records.append(json.dumps({"type": "assistant", "message": {"content": content}}))
    records.append(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result_text,
                "session_id": session_id,
            }
        )
    )
    return "\n".join(records)


class TestExtractMissingTokenHints:
    def test_extracts_token_and_path(self):
        stdout = _ndjson_with_write("plan summary\n%%ORDER_UP%%", ["/tmp/out.md"])
        hints = _extract_missing_token_hints(stdout, [r"plan_path\s*=\s*/.+"])
        assert hints == [("plan_path", "/tmp/out.md")]

    def test_returns_empty_when_pattern_satisfied(self):
        stdout = _ndjson_with_write("plan_path = /tmp/out.md\n%%ORDER_UP%%", ["/tmp/out.md"])
        hints = _extract_missing_token_hints(stdout, [r"plan_path\s*=\s*/.+"])
        assert hints == []

    def test_returns_empty_for_non_path_patterns(self):
        stdout = _ndjson_with_write("%%ORDER_UP%%", ["/tmp/out.md"])
        hints = _extract_missing_token_hints(stdout, [r"verdict\s*=\s*\w+"])
        assert hints == []

    def test_uses_last_write_path(self):
        stdout = _ndjson_with_write("%%ORDER_UP%%", ["/tmp/first.md", "/tmp/final.md"])
        hints = _extract_missing_token_hints(stdout, [r"plan_path\s*=\s*/.+"])
        assert hints == [("plan_path", "/tmp/final.md")]


class TestContractNudge:
    """Integration tests for the contract recovery nudge in run_headless_core.

    The nudge fires when CONTRACT_RECOVERY is triggered with a valid session_id.
    CONTRACT_RECOVERY requires synthesis to have failed first. For CHANNEL_A/B
    sessions, synthesis is skipped (gated on UNMONITORED), so Write evidence
    with file_path triggers CONTRACT_RECOVERY while _extract_missing_token_hints
    can still find the file_path for hints.
    """

    def _main_session_ndjson(
        self, marker: str, *, include_token: bool = False, session_id: str = "sess-main"
    ) -> str:
        """Build NDJSON for a CHANNEL_A-confirmed session with Write evidence."""
        result_text = "plan summary\n"
        if include_token:
            result_text += "plan_path = /tmp/out.md\n"
        result_text += marker
        return _ndjson_with_write(result_text, ["/tmp/out.md"], session_id=session_id)

    def _main_subprocess_result(
        self, marker: str, *, session_id: str = "sess-main"
    ) -> SubprocessResult:
        """Build a SubprocessResult with CHANNEL_A confirmation to bypass synthesis."""
        return SubprocessResult(
            returncode=0,
            stdout=self._main_session_ndjson(marker, session_id=session_id),
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )

    def _nudge_response_ndjson(self, marker: str, *, include_token: bool = True) -> str:
        """Build NDJSON for a nudge response."""
        if include_token:
            result_text = f"plan_path = /tmp/out.md\n{marker}"
        else:
            result_text = f"I cannot do that.\n{marker}"
        return json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": result_text,
                "session_id": "sess-main",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )

    @pytest.mark.anyio
    async def test_nudge_fires_on_contract_recovery(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._main_subprocess_result(marker))
        tool_ctx.runner.push(
            SubprocessResult(
                0, self._nudge_response_ndjson(marker), "", TerminationReason.NATURAL_EXIT, pid=2
            )
        )
        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert result.success is True
        assert result.needs_retry is False
        assert "plan_path = /tmp/out.md" in result.result

    @pytest.mark.anyio
    async def test_nudge_failure_falls_through(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._main_subprocess_result(marker))
        tool_ctx.runner.push(
            SubprocessResult(
                0,
                self._nudge_response_ndjson(marker, include_token=False),
                "",
                TerminationReason.NATURAL_EXIT,
                pid=2,
            )
        )
        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert result.retry_reason == RetryReason.CONTRACT_RECOVERY
        assert result.needs_retry is True

    @pytest.mark.anyio
    async def test_nudge_timeout_falls_through(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._main_subprocess_result(marker))
        tool_ctx.runner.push(
            SubprocessResult(1, "", "timeout", TerminationReason.TIMED_OUT, pid=2)
        )
        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert result.retry_reason == RetryReason.CONTRACT_RECOVERY
        assert result.needs_retry is True

    @pytest.mark.anyio
    async def test_nudge_skips_when_budget_exhausted(self, tool_ctx):
        from datetime import UTC, datetime

        from autoskillit.core import FailureRecord
        from autoskillit.execution.headless import run_headless_core
        from autoskillit.pipeline.audit import DefaultAuditLog

        marker = tool_ctx.config.run_skill.completion_marker
        audit = DefaultAuditLog()
        for _ in range(4):
            audit.record_failure(
                FailureRecord(  # type: ignore[arg-type]
                    timestamp=datetime.now(UTC).isoformat(),
                    skill_command="/autoskillit:make-plan foo",
                    exit_code=-1,
                    subtype="stale",
                    needs_retry=True,
                    retry_reason="stale",
                    stderr="",
                )
            )
        tool_ctx.audit = audit
        tool_ctx.runner.push(self._main_subprocess_result(marker))
        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert result.retry_reason == RetryReason.BUDGET_EXHAUSTED
        assert len(tool_ctx.runner.call_args_list) == 1

    @pytest.mark.anyio
    async def test_nudge_skips_when_no_session_id(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        ndjson = _ndjson_with_write(f"plan summary\n{marker}", ["/tmp/out.md"], session_id="")
        tool_ctx.runner.push(
            SubprocessResult(
                0,
                ndjson,
                "",
                TerminationReason.NATURAL_EXIT,
                pid=1,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
        )
        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert result.retry_reason == RetryReason.CONTRACT_RECOVERY
        assert len(tool_ctx.runner.call_args_list) == 1

    @pytest.mark.anyio
    async def test_nudge_resume_cmd_uses_correct_session_id(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._main_subprocess_result(marker, session_id="sess-abc-123"))
        tool_ctx.runner.push(
            SubprocessResult(
                0, self._nudge_response_ndjson(marker), "", TerminationReason.NATURAL_EXIT, pid=2
            )
        )
        await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert len(tool_ctx.runner.call_args_list) == 2
        nudge_cmd = tool_ctx.runner.call_args_list[1][0]
        assert "--resume" in nudge_cmd
        resume_idx = nudge_cmd.index("--resume")
        assert nudge_cmd[resume_idx + 1] == "sess-abc-123"

    @pytest.mark.anyio
    async def test_nudge_token_usage_merged(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        main_ndjson_records = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "id": "t0",
                                "input": {"file_path": "/tmp/out.md"},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": f"plan summary\n{marker}",
                    "session_id": "sess-tok",
                    "usage": {"input_tokens": 1000, "output_tokens": 500},
                }
            ),
        ]
        main_ndjson = "\n".join(main_ndjson_records)

        nudge_ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"plan_path = /tmp/out.md\n{marker}",
                "session_id": "sess-tok",
                "usage": {"input_tokens": 200, "output_tokens": 100},
            }
        )

        tool_ctx.runner.push(
            SubprocessResult(
                0,
                main_ndjson,
                "",
                TerminationReason.NATURAL_EXIT,
                pid=1,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
        )
        tool_ctx.runner.push(
            SubprocessResult(0, nudge_ndjson, "", TerminationReason.NATURAL_EXIT, pid=2)
        )

        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
            step_name="test-step",
        )
        assert result.success is True
        assert result.token_usage is not None
        assert result.token_usage.get("input_tokens", 0) >= 1200
        assert result.token_usage.get("output_tokens", 0) >= 600

    @pytest.mark.anyio
    async def test_nudge_exception_falls_through(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        tool_ctx.runner.push(self._main_subprocess_result(marker))
        # Empty stdout nudge → patterns not found → falls through
        tool_ctx.runner.push(
            SubprocessResult(1, "", "RuntimeError", TerminationReason.NATURAL_EXIT, pid=2)
        )
        result = await run_headless_core(
            "/autoskillit:make-plan foo",
            cwd="/tmp",
            ctx=tool_ctx,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert result.retry_reason == RetryReason.CONTRACT_RECOVERY
        assert result.needs_retry is True


def test_build_skill_result_surfaces_last_stop_reason():
    ndjson = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"stop_reason": "end_turn", "content": [], "usage": {}},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": "s1",
                }
            ),
        ]
    )
    result = _make_result(returncode=0, stdout=ndjson)
    sr = _build_skill_result(result)
    assert sr.last_stop_reason == "end_turn"
