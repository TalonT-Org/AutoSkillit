"""Protocol conformance for CodingAgentBackend and sub-protocols.

Contract tests verifying @runtime_checkable decoration, isinstance
conformance, and build_skill_session_cmd signature/delegation contracts
for CodingAgentBackend, StreamParser, ResultParser, and ClaudeCodeBackend.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


# -- Importability ----------------------------------------------------------


def test_backend_protocols_importable_from_core():
    from autoskillit.core import (  # noqa: F401
        CodingAgentBackend,
        ResultParser,
        StreamParser,
    )


# -- @runtime_checkable -----------------------------------------------------


def test_stream_parser_is_runtime_checkable():
    from autoskillit.core import StreamParser

    assert getattr(StreamParser, "_is_runtime_protocol", False), (
        "StreamParser not decorated with @runtime_checkable"
    )


def test_result_parser_is_runtime_checkable():
    from autoskillit.core import ResultParser

    assert getattr(ResultParser, "_is_runtime_protocol", False), (
        "ResultParser not decorated with @runtime_checkable"
    )


def test_coding_agent_backend_is_runtime_checkable():
    from autoskillit.core import CodingAgentBackend

    assert getattr(CodingAgentBackend, "_is_runtime_protocol", False), (
        "CodingAgentBackend not decorated with @runtime_checkable"
    )


# -- isinstance conformance: ClaudeCodeBackend ------------------------------


def test_claude_code_backend_satisfies_coding_agent_backend():
    from autoskillit.core import CodingAgentBackend
    from autoskillit.execution.backends import ClaudeCodeBackend

    assert isinstance(ClaudeCodeBackend(), CodingAgentBackend)


def test_claude_code_backend_stream_parser_satisfies_protocol():
    from autoskillit.core import StreamParser
    from autoskillit.execution.backends import ClaudeCodeBackend

    assert isinstance(ClaudeCodeBackend().stream_parser(), StreamParser)


def test_claude_code_backend_result_parser_satisfies_protocol():
    from autoskillit.core import ResultParser
    from autoskillit.execution.backends import ClaudeCodeBackend

    assert isinstance(ClaudeCodeBackend().result_parser(), ResultParser)


def test_claude_code_backend_stream_parser_accepts_completion_marker():
    from autoskillit.core import StreamParser
    from autoskillit.execution.backends import ClaudeCodeBackend

    parser = ClaudeCodeBackend().stream_parser(completion_marker="%%TEST%%")
    assert isinstance(parser, StreamParser)


def test_claude_code_backend_stream_parser_forwards_completion_marker():
    from autoskillit.execution.backends import ClaudeCodeBackend

    parser = ClaudeCodeBackend().stream_parser(completion_marker="%%ORDER_UP%%")
    assert parser.completion_marker == "%%ORDER_UP%%"


# -- Signature conformance: build_skill_session_cmd ---------------------------


def test_build_skill_session_cmd_positional_param_names_match_protocol():
    import inspect

    from autoskillit.core import CodingAgentBackend
    from autoskillit.execution.backends import ClaudeCodeBackend

    proto_sig = inspect.signature(CodingAgentBackend.build_skill_session_cmd)
    impl_sig = inspect.signature(ClaudeCodeBackend.build_skill_session_cmd)

    proto_names = [
        n
        for n, p in proto_sig.parameters.items()
        if n != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    impl_names = [
        n
        for n, p in impl_sig.parameters.items()
        if n != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ][: len(proto_names)]

    assert proto_names == impl_names, (
        f"Positional param name mismatch: protocol={proto_names}, impl={impl_names}"
    )


def test_build_skill_session_cmd_return_annotation_is_cmdspec():
    from typing import get_type_hints

    from autoskillit.core import CmdSpec
    from autoskillit.execution.backends import ClaudeCodeBackend

    hints = get_type_hints(ClaudeCodeBackend.build_skill_session_cmd)
    assert hints["return"] is CmdSpec, (
        f"Return annotation should be CmdSpec, got {hints['return']}"
    )


def test_build_skill_session_cmd_protocol_shape_call_succeeds():
    from autoskillit.core import CmdSpec, SkillSessionConfig
    from autoskillit.execution.backends import ClaudeCodeBackend

    result = ClaudeCodeBackend().build_skill_session_cmd(
        "/test-skill", "/tmp", SkillSessionConfig()
    )
    assert isinstance(result, CmdSpec)


def test_build_skill_session_cmd_config_delegates_to_impl():
    from pathlib import Path
    from unittest.mock import patch

    from autoskillit.core import (
        CmdSpec,
        DirectInstall,
        OutputFormat,
        SessionCheckpoint,
        SkillSessionConfig,
    )
    from autoskillit.execution.backends import ClaudeCodeBackend

    config = SkillSessionConfig(
        completion_marker="%%VERIFY%%",
        model="sonnet",
        plugin_source=DirectInstall(plugin_dir=Path("/p")),
        output_format=OutputFormat.STREAM_JSON,
        exit_after_stop_delay_ms=120000,
        stream_idle_timeout_ms=30000,
        scenario_step_name="step-verify",
        temp_dir_relpath=".autoskillit/temp",
        allowed_write_prefix="/tmp/verify",
        provider_extras={"EXTRA": "val"},
        profile_name="verify-profile",
        resume_session_id="sess-1",
        resume_checkpoint=SessionCheckpoint(step_name="chk"),
        resume_message="resume-msg",
    )
    sentinel = CmdSpec(cmd=("sentinel",), env={})

    with patch.object(
        ClaudeCodeBackend, "_build_skill_session_cmd_impl", return_value=sentinel
    ) as mock_impl:
        backend = ClaudeCodeBackend()
        result = backend.build_skill_session_cmd("/test", "/work", config)

    assert result is sentinel
    mock_impl.assert_called_once()
    args = mock_impl.call_args.args
    assert args == ("/test",), f"skill_command not forwarded: {args}"
    kw = mock_impl.call_args.kwargs
    assert kw["cwd"] == "/work"
    assert kw["completion_marker"] == config.completion_marker
    assert kw["model"] == config.model
    assert kw["plugin_source"] == config.plugin_source
    assert kw["output_format"] == config.output_format
    assert kw["add_dirs"] == config.add_dirs
    assert kw["exit_after_stop_delay_ms"] == config.exit_after_stop_delay_ms
    assert kw["stream_idle_timeout_ms"] == config.stream_idle_timeout_ms
    assert kw["scenario_step_name"] == config.scenario_step_name
    assert kw["temp_dir_relpath"] == config.temp_dir_relpath
    assert kw["allowed_write_prefix"] == config.allowed_write_prefix
    assert kw["provider_extras"] == config.provider_extras
    assert kw["profile_name"] == config.profile_name
    assert kw["resume_session_id"] == config.resume_session_id
    assert kw["resume_checkpoint"] == config.resume_checkpoint
    assert kw["resume_message"] == config.resume_message


def test_build_skill_session_cmd_impl_exists():
    from autoskillit.execution.backends import ClaudeCodeBackend

    assert hasattr(ClaudeCodeBackend, "_build_skill_session_cmd_impl")
    assert callable(getattr(ClaudeCodeBackend, "_build_skill_session_cmd_impl"))


def test_build_interactive_cmd_satisfies_protocol_claude():
    from autoskillit.core import CodingAgentBackend
    from autoskillit.execution.backends import ClaudeCodeBackend

    assert isinstance(ClaudeCodeBackend(), CodingAgentBackend)


def test_build_interactive_cmd_codex_raises_not_implemented():
    from autoskillit.execution.backends import CodexBackend

    with pytest.raises(NotImplementedError, match="P6-A3"):
        CodexBackend().build_interactive_cmd()


def test_build_interactive_cmd_signature_shape():
    import inspect

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend

    sig = inspect.signature(CodingAgentBackend.build_interactive_cmd)
    params = sig.parameters

    assert "order_mode" not in params, "order_mode must not be in signature"
    assert params["system_prompt"].default is None
    for name, param in params.items():
        if name == "self":
            continue
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, f"{name} must be KEYWORD_ONLY"
