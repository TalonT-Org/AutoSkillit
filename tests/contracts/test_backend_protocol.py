"""Protocol conformance for CodingAgentBackend and sub-protocols.

Contract tests verifying @runtime_checkable decoration and isinstance
conformance for CodingAgentBackend, StreamParser, ResultParser, and
ClaudeCodeBackend.
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
