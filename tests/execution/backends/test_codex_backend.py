from __future__ import annotations

import json
from enum import StrEnum

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    BackendEventKind,
    CmdSpec,
    CodexEventData,
    CodingAgentBackend,
    EnvPolicy,
    ResultParser,
    SessionLocator,
    StreamParser,
)
from autoskillit.execution.backends.codex import (
    CodexBackend,
    CodexEnvPolicy,
    CodexFlags,
    CodexResultParser,
    CodexSessionLocator,
    CodexStreamParser,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexFlags:
    def test_is_str_enum(self) -> None:
        assert issubclass(CodexFlags, StrEnum)

    def test_str_json_equals_double_dash_json(self) -> None:
        assert str(CodexFlags.JSON) == "--json"

    def test_all_members_present(self) -> None:
        expected = {
            "JSON",
            "SANDBOX",
            "ASK_FOR_APPROVAL",
            "ASK_FOR_APPROVAL_SHORT",
            "MODEL",
            "MODEL_SHORT",
            "ADD_DIR",
            "IGNORE_USER_CONFIG",
            "EPHEMERAL",
            "RESUME_SUBCOMMAND",
            "LAST",
        }
        actual = {m.name for m in CodexFlags}
        assert actual == expected
        assert len(set(CodexFlags)) == len(expected)


class TestCodexBackend:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_name_property(self) -> None:
        assert CodexBackend().name == AGENT_BACKEND_CODEX

    def test_capabilities_channel_b_false(self) -> None:
        assert CodexBackend().capabilities.channel_b_capable is False

    def test_capabilities_skill_injection_false(self) -> None:
        assert CodexBackend().capabilities.skill_injection_capable is False

    def test_capabilities_pty_required_false(self) -> None:
        assert CodexBackend().capabilities.pty_required is False

    def test_capabilities_session_resume_true(self) -> None:
        assert CodexBackend().capabilities.session_resume_capable is True

    def test_capabilities_supports_thinking_blocks_false(self) -> None:
        assert CodexBackend().capabilities.supports_thinking_blocks is False

    def test_capabilities_supports_claude_format_stdout_false(self) -> None:
        assert CodexBackend().capabilities.supports_claude_format_stdout is False

    def test_capabilities_exit_code_is_terminal_true(self) -> None:
        assert CodexBackend().capabilities.exit_code_is_terminal is True

    def test_capabilities_completion_record_types(self) -> None:
        expected = frozenset({"turn.completed", "turn.failed", "error"})
        assert CodexBackend().capabilities.completion_record_types == expected

    def test_capabilities_session_record_types_empty(self) -> None:
        assert CodexBackend().capabilities.session_record_types == frozenset()

    def test_binary_name(self) -> None:
        assert CodexBackend().binary_name() == "codex"

    def test_version_cmd(self) -> None:
        assert CodexBackend().version_cmd() == ("codex", "--version")


class TestCodexBackendCommands:
    def test_build_headless_cmd_codex_at_0(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[0] == "codex"

    def test_build_headless_cmd_exec_at_1(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[1] == "exec"

    def test_build_headless_cmd_has_json_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--json" in spec.cmd

    def test_build_headless_cmd_has_sandbox_flag(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "--sandbox" in spec.cmd
        idx = spec.cmd.index("--sandbox")
        assert spec.cmd[idx + 1] == "workspace-write"

    def test_build_headless_cmd_has_approval_never(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert "-a" in spec.cmd
        idx = spec.cmd.index("-a")
        assert spec.cmd[idx + 1] == "never"

    def test_build_headless_cmd_prompt_is_last(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff")
        assert spec.cmd[-1] == "do stuff"

    def test_build_headless_cmd_with_model(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", model="o3")
        assert "--model" in spec.cmd
        idx = spec.cmd.index("--model")
        assert spec.cmd[idx + 1] == "o3"

    def test_build_headless_cmd_returns_cmd_spec(self) -> None:
        spec = CodexBackend().build_headless_cmd("x")
        assert isinstance(spec, CmdSpec)
        assert isinstance(spec.cmd, tuple)

    def test_build_headless_cmd_with_env_extras(self) -> None:
        spec = CodexBackend().build_headless_cmd("do stuff", env_extras={"FOO": "bar"})
        assert spec.env.get("FOO") == "bar"

    def test_build_cmd_delegates_to_headless(self) -> None:
        backend = CodexBackend()
        spec = backend.build_cmd("do stuff", "/work")
        assert spec.cmd[0] == "codex"
        assert spec.cwd == "/work"

    def test_build_resume_cmd_with_session_id(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="sess-123", prompt="continue")
        assert spec.cmd[0] == "codex"
        assert spec.cmd[1] == "exec"
        assert "resume" in spec.cmd
        assert "sess-123" in spec.cmd
        assert spec.cmd[-1] == "continue"

    def test_build_resume_cmd_empty_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            CodexBackend().build_resume_cmd(resume_session_id="", prompt="continue")

    def test_build_resume_cmd_with_env_extras(self) -> None:
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="s1", prompt="go", env_extras={"FOO": "bar"}
        )
        assert spec.env.get("FOO") == "bar"

    def test_build_resume_cmd_env_includes_os_environ(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "PATH" in spec.env

    def test_build_resume_cmd_has_json_flag(self) -> None:
        spec = CodexBackend().build_resume_cmd(resume_session_id="s1", prompt="go")
        assert "--json" in spec.cmd

    def test_build_interactive_cmd_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            CodexBackend().build_interactive_cmd()


class TestCodexBackendFactories:
    def test_stream_parser_returns_stream_parser(self) -> None:
        assert isinstance(CodexBackend().stream_parser(), StreamParser)

    def test_result_parser_returns_result_parser(self) -> None:
        assert isinstance(CodexBackend().result_parser(), ResultParser)

    def test_result_parser_is_codex_result_parser(self) -> None:
        assert isinstance(CodexBackend().result_parser(), CodexResultParser)

    def test_env_policy_returns_env_policy(self) -> None:
        assert isinstance(CodexBackend().env_policy(), EnvPolicy)

    def test_session_locator_returns_session_locator(self) -> None:
        locator = CodexBackend().session_locator()
        assert isinstance(locator, SessionLocator)

    def test_session_locator_locate_returns_none(self) -> None:
        locator = CodexBackend().session_locator()
        assert locator.locate_session("any-id") is None

    def test_write_tool_names_returns_frozenset(self) -> None:
        assert isinstance(CodexBackend().write_tool_names(), frozenset)

    def test_stream_parser_factory_passes_completion_marker(self) -> None:
        parser = CodexBackend().stream_parser(completion_marker="%%DONE%%")
        assert isinstance(parser, CodexStreamParser)
        assert parser.completion_marker == "%%DONE%%"

    def test_stream_parser_factory_default_empty_marker(self) -> None:
        parser = CodexBackend().stream_parser()
        assert isinstance(parser, CodexStreamParser)
        assert parser.completion_marker == ""


class TestCodexStreamParser:
    def test_parse_line_empty_returns_none(self) -> None:
        parser = CodexStreamParser()
        assert parser.parse_line("") is None

    def test_parse_line_invalid_json_returns_none(self) -> None:
        parser = CodexStreamParser()
        assert parser.parse_line("not json") is None

    def test_parse_line_thread_started_yields_session_meta(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "thread.started", "thread_id": "t1"})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.SESSION_META
        assert event.session_id == "t1"
        assert event.is_terminal is False

    def test_parse_line_turn_completed_yields_completion(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.COMPLETION
        assert event.is_terminal is True

    def test_parse_line_turn_failed_yields_completion(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "turn.failed", "error": {"message": "fail"}})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.COMPLETION
        assert event.is_terminal is True

    def test_parse_line_error_yields_error_event(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "error", "message": "crash"})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.ERROR
        assert event.is_terminal is True

    def test_parse_line_item_completed_yields_ignored(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "item.completed", "item": {"type": "reasoning"}})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.IGNORED

    def test_completion_marker_detection_in_message(self) -> None:
        parser = CodexStreamParser(completion_marker="%%DONE%%")
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "result\n%%DONE%%"}],
                },
            }
        )
        parser.parse_line(msg_line)
        done_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(done_line)
        assert event is not None
        assert event.has_marker is True

    def test_no_marker_when_completion_marker_empty(self) -> None:
        parser = CodexStreamParser()
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "%%DONE%%"}],
                },
            }
        )
        parser.parse_line(msg_line)
        done_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(done_line)
        assert event is not None
        assert event.has_marker is False

    def test_turn_completed_backend_data_is_codex_event_data(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}})
        event = parser.parse_line(line)
        assert event is not None
        assert isinstance(event.backend_data, CodexEventData)

    def test_parse_line_item_completed_message_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "message", "content": [{"type": "text", "text": "hello"}]},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT

    def test_parse_line_item_completed_file_change_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "file_change", "path": "src/foo.py"}}
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT

    def test_parse_line_item_completed_function_call_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "function_call", "name": "Bash"}}
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT

    def test_parse_line_item_completed_message_has_backend_data(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "message", "content": [{"type": "text", "text": "hello"}]},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.record_type == "item.completed"
        assert event.backend_data.item_type == "message"

    def test_parse_line_item_completed_file_change_backend_data_item_type(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "file_change", "path": "src/foo.py"}}
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.backend_data is not None
        assert event.backend_data.item_type == "file_change"

    def test_parse_line_item_completed_function_call_backend_data_item_type(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "function_call", "name": "Bash"}}
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.backend_data is not None
        assert event.backend_data.item_type == "function_call"

    def test_parse_line_item_completed_message_not_terminal(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "message", "content": [{"type": "text", "text": "hello"}]},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.is_terminal is False


class TestCodexStreamParserConformance:
    def test_isinstance_stream_parser_protocol(self) -> None:
        assert isinstance(CodexStreamParser(""), StreamParser)


class TestCodexEnvPolicy:
    def test_build_env_passes_through_base(self) -> None:
        policy = CodexEnvPolicy()
        result = policy.build_env({"PATH": "/usr/bin", "HOME": "/root"})
        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/root"


class TestCodexSessionLocator:
    def test_locate_session_returns_none(self) -> None:
        locator = CodexSessionLocator()
        assert locator.locate_session("any-session-id") is None

    def test_satisfies_session_locator_protocol(self) -> None:
        assert isinstance(CodexSessionLocator(), SessionLocator)


class TestCodexImportContract:
    def test_import_codex_flags_from_module(self) -> None:
        from autoskillit.execution.backends.codex import CodexFlags

        assert issubclass(CodexFlags, StrEnum)

    def test_import_codex_backend_from_package(self) -> None:
        from autoskillit.execution.backends import CodexBackend

        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_import_codex_backend_from_execution(self) -> None:
        from autoskillit.execution import CodexBackend

        assert isinstance(CodexBackend(), CodingAgentBackend)

    def test_codex_backend_not_in_core_types(self) -> None:
        from autoskillit.core.types import __all__ as core_all

        assert "CodexBackend" not in core_all
