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


# -- isinstance conformance: CodexBackend ------------------------------------


def test_codex_backend_satisfies_coding_agent_backend():
    from autoskillit.core import CodingAgentBackend
    from autoskillit.execution.backends import CodexBackend

    assert isinstance(CodexBackend(), CodingAgentBackend)


def test_codex_backend_stream_parser_satisfies_protocol():
    from autoskillit.core import StreamParser
    from autoskillit.execution.backends import CodexBackend

    assert isinstance(CodexBackend().stream_parser(), StreamParser)


def test_codex_backend_result_parser_satisfies_protocol():
    from autoskillit.core import ResultParser
    from autoskillit.execution.backends import CodexBackend

    assert isinstance(CodexBackend().result_parser(), ResultParser)


def test_codex_backend_env_policy_satisfies_protocol():
    from autoskillit.core import EnvPolicy
    from autoskillit.execution.backends import CodexBackend

    assert isinstance(CodexBackend().env_policy(), EnvPolicy)


# -- Signature conformance: EnvPolicy.build_env -------------------------------


def test_env_policy_build_env_signature_includes_extras_and_required():
    import inspect

    from autoskillit.core import EnvPolicy

    sig = inspect.signature(EnvPolicy.build_env)
    params = sig.parameters

    assert "extras" in params, "EnvPolicy.build_env must have 'extras' parameter"
    assert params["extras"].default is None
    assert "required" in params, "EnvPolicy.build_env must have 'required' parameter"
    assert params["required"].default is None


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
        allowed_write_prefixes=("/tmp/verify/",),
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
    assert kw["allowed_write_prefixes"] == config.allowed_write_prefixes
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


def test_build_interactive_cmd_codex_returns_cmd_spec():
    from autoskillit.core import CmdSpec
    from autoskillit.execution.backends import CodexBackend

    spec = CodexBackend().build_interactive_cmd()
    assert isinstance(spec, CmdSpec)


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


# -- version() return type ---------------------------------------------------


def test_claude_code_backend_version_returns_str():
    from unittest.mock import MagicMock, patch

    from autoskillit.execution.backends import ClaudeCodeBackend

    with patch(
        "autoskillit.execution.backends.claude.subprocess.run",
        return_value=MagicMock(stdout="1.2.3\n", stderr="", returncode=0),
    ):
        result = ClaudeCodeBackend().version()

    assert isinstance(result, str)


def test_codex_backend_version_returns_str():
    from unittest.mock import MagicMock, patch

    from autoskillit.execution.backends import CodexBackend

    with patch(
        "autoskillit.execution.backends.codex.subprocess.run",
        return_value=MagicMock(stdout="0.1.0\n", stderr="", returncode=0),
    ):
        result = CodexBackend().version()

    assert isinstance(result, str)


# -- list_plugins() return type -----------------------------------------------


def test_claude_code_backend_list_plugins_returns_list():
    from unittest.mock import patch

    from autoskillit.execution.backends import ClaudeCodeBackend

    fake_json = '{"version": 2, "plugins": {"pkg": [{"version": "1.0"}]}}'
    with patch(
        "autoskillit.execution.backends.claude.Path.read_text",
        return_value=fake_json,
    ):
        result = ClaudeCodeBackend().list_plugins()

    assert isinstance(result, list)


def test_codex_backend_list_plugins_returns_list():
    from unittest.mock import MagicMock, patch

    from autoskillit.execution.backends import CodexBackend

    with patch(
        "autoskillit.execution.backends.codex.subprocess.run",
        return_value=MagicMock(stdout='[{"name": "test"}]\n', stderr="", returncode=0),
    ):
        result = CodexBackend().list_plugins()

    assert isinstance(result, list)


# -- validate_skill_content() return type and behavior ------------------------


def test_claude_code_backend_validate_skill_content_returns_list():
    from autoskillit.execution.backends import ClaudeCodeBackend

    result = ClaudeCodeBackend().validate_skill_content("")
    assert isinstance(result, list)


def test_validate_skill_content_reports_missing_required_fields():
    from autoskillit.execution.backends import ClaudeCodeBackend

    backend = ClaudeCodeBackend()
    required = backend.capabilities.required_skill_fields
    assert required, "capabilities.required_skill_fields must not be empty"

    content = "---\nunrelated_key: value\n---\nBody text"
    result = backend.validate_skill_content(content)
    assert len(result) > 0, "should report errors for missing required fields"


def test_validate_skill_content_passes_for_valid_content():
    from autoskillit.execution.backends import ClaudeCodeBackend

    backend = ClaudeCodeBackend()
    required = backend.capabilities.required_skill_fields

    frontmatter_lines = [f"{field}: test_value" for field in required]
    content = "---\n" + "\n".join(frontmatter_lines) + "\n---\nBody text"
    result = backend.validate_skill_content(content)
    assert result == [], f"expected no errors for valid content, got {result}"
