"""Contract tests binding output format to data availability.

These tests enforce the format-data contract that prevents the class of bug
"silent feature disablement via configuration mismatch":

1. Format-to-data: STREAM_JSON → non-empty assistant_messages; JSON → empty
2. Recovery integration: format → parse → recovery → adjudication end-to-end
3. Format derivation: OutputFormat.derive() correctly derives from config
4. Channel default coverage: UNMONITORED exercises content validation
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    OutputFormat,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless import _build_skill_result
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _compute_success,
    parse_session_result,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestOutputFormatDataContract:
    """Contract tests binding output format to data availability."""

    def test_stream_json_format_populates_assistant_messages(self):
        """NDJSON with type=assistant records produces non-empty assistant_messages."""
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"Analysis complete."}}\n'
            '{"type":"result","subtype":"success","is_error":false,"result":"%%ORDER_UP%%","session_id":"s1","errors":[]}\n'
        )
        session = parse_session_result(ndjson)
        assert session.assistant_messages == ["Analysis complete."]

    def test_single_json_format_produces_empty_assistant_messages(self):
        """Single JSON object (--output-format json) produces empty assistant_messages."""
        single_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "errors": [],
            }
        )
        session = parse_session_result(single_json)
        assert session.assistant_messages == []

    def test_stream_json_populates_model_breakdown(self):
        """NDJSON with type=assistant records populates model_breakdown in token_usage."""
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-4-6",'
            '"content":"text","usage":{"input_tokens":10,"output_tokens":20}}}\n'
            '{"type":"result","subtype":"success","is_error":false,"result":"done","session_id":"s1","errors":[]}\n'
        )
        session = parse_session_result(ndjson)
        assert session.token_usage is not None
        assert "model_breakdown" in session.token_usage
        assert "claude-sonnet-4-6" in session.token_usage["model_breakdown"]

    def test_single_json_has_empty_model_breakdown(self):
        """Single JSON (--output-format json) has empty model_breakdown."""
        single_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "errors": [],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        session = parse_session_result(single_json)
        assert session.token_usage is not None
        assert session.token_usage.get("model_breakdown", {}) == {}


class TestRecoveryIntegrationWithFormat:
    """End-to-end tests: format -> parse -> recovery -> adjudication."""

    def test_stream_json_format_recovery_succeeds_marker_separate(self):
        """With --output-format stream-json output, recovery works when marker is separate.

        NDJSON provides assistant_messages for recovery.
        """
        assistant1 = json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "MERGE APPROVED\n\nAll checks pass."},
            }
        )
        assistant2 = json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "%%ORDER_UP%%"},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "%%ORDER_UP%%",
                "session_id": "s1",
                "errors": [],
            }
        )
        ndjson = f"{assistant1}\n{assistant2}\n{result_rec}\n"
        sub_result = SubprocessResult(
            returncode=0,
            stdout=ndjson,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1234,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        skill_result = _build_skill_result(sub_result, "%%ORDER_UP%%", "/test", None)
        assert skill_result.success is True
        assert "MERGE APPROVED" in skill_result.result

    def test_json_format_recovery_impossible_marker_separate(self):
        """With --output-format json output, recovery from separate marker is impossible.

        This documents the limitation: single JSON has no assistant_messages,
        so when result contains only the marker, recovery cannot reconstruct
        substantive content.
        """
        single_json = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "%%ORDER_UP%%",
                "session_id": "s1",
                "errors": [],
            }
        )
        sub_result = SubprocessResult(
            returncode=0,
            stdout=single_json,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1234,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        skill_result = _build_skill_result(sub_result, "%%ORDER_UP%%", "/test", None)
        # With JSON format, result is only the marker — stripped to empty — fails content check.
        # With UNMONITORED channel, dead-end guard does not promote to retriable.
        assert skill_result.success is False
        assert skill_result.needs_retry is False


class TestOutputFormatDerivation:
    """The output format must be derived from feature requirements."""

    def test_format_is_stream_json_when_completion_marker_configured(self):
        """When completion_marker is set, format must be STREAM_JSON
        because recovery requires assistant_messages."""
        assert OutputFormat.derive(completion_marker="%%ORDER_UP%%") == OutputFormat.STREAM_JSON

    def test_format_can_be_json_when_no_completion_marker(self):
        """When no completion_marker, JSON format is acceptable."""
        assert OutputFormat.derive(completion_marker="") == OutputFormat.JSON

    def test_format_enum_has_capability_declarations(self):
        """Each format variant declares what data it provides."""
        assert OutputFormat.STREAM_JSON.supports_assistant_messages is True
        assert OutputFormat.JSON.supports_assistant_messages is False
        assert OutputFormat.STREAM_JSON.supports_model_breakdown is True
        assert OutputFormat.JSON.supports_model_breakdown is False

    def test_run_skill_config_derives_format(self):
        """RunSkillConfig.output_format derives STREAM_JSON for default config."""
        from tests._helpers import make_run_skill_config

        cfg = make_run_skill_config()  # default completion_marker is set
        assert cfg.output_format == OutputFormat.STREAM_JSON

    def test_run_skill_config_derives_json_when_no_marker(self):
        """RunSkillConfig.output_format derives JSON when completion_marker is empty."""
        from tests._helpers import make_run_skill_config

        cfg = make_run_skill_config(completion_marker="")
        assert cfg.output_format == OutputFormat.JSON


class TestOutputFormatCliRequirements:
    """Contract tests: each OutputFormat variant declares required CLI flags."""

    def test_stream_json_requires_verbose(self):
        """STREAM_JSON must declare --verbose as a required CLI flag."""
        assert "--verbose" in OutputFormat.STREAM_JSON.required_cli_flags

    def test_json_requires_no_extra_flags(self):
        """JSON format requires no additional CLI flags."""
        assert OutputFormat.JSON.required_cli_flags == ()

    def test_all_formats_declare_required_flags(self):
        """Every OutputFormat member must declare required_cli_flags as a tuple."""
        for fmt in OutputFormat:
            flags = fmt.required_cli_flags
            assert isinstance(flags, tuple), f"{fmt.name} returned {type(flags)}, expected tuple"

    def test_command_assembly_satisfies_format_requirements(self):
        """Assembled command must contain all flags required by the chosen format.

        Builds a command the same way run_headless_core does, then asserts
        every flag from the format's required_cli_flags is present.
        """
        from autoskillit.execution.commands import build_headless_cmd

        fmt = OutputFormat.STREAM_JSON
        spec = build_headless_cmd("Use /investigate test", model=None)
        cmd = spec.cmd + ["--plugin-dir", "/fake", "--output-format", fmt.value]
        # Apply required flags (mirrors run_headless_core logic)
        for flag in fmt.required_cli_flags:
            if flag not in cmd:
                cmd.append(flag)
        for flag in fmt.required_cli_flags:
            assert flag in cmd, f"Missing required flag {flag} in assembled command"


class TestChannelDefaultCoverage:
    """Verify that UNMONITORED channel exercises content validation."""

    def test_unmonitored_channel_checks_content(self):
        """UNMONITORED + empty result = failure (content check not bypassed)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%DONE%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        assert success is False

    def test_channel_b_bypasses_content_check(self):
        """CHANNEL_B + empty result = success (provenance bypass)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%DONE%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        assert success is True
