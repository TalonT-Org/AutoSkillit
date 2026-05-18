"""Tests for StreamParser, ResultParser, EnvPolicy, SessionLocator, CodingAgentBackend."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_all_backend_protocols_are_runtime_checkable():
    from autoskillit.core import (
        CodingAgentBackend,
        EnvPolicy,
        ResultParser,
        SessionLocator,
        StreamParser,
    )

    for proto in (StreamParser, ResultParser, EnvPolicy, SessionLocator, CodingAgentBackend):
        assert getattr(proto, "_is_runtime_protocol", False), (
            f"{proto.__name__} must be @runtime_checkable"
        )


def test_coding_agent_backend_has_name_property():
    from autoskillit.core import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "name")


def test_coding_agent_backend_has_capabilities_property():
    from autoskillit.core import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "capabilities")


def test_coding_agent_backend_has_build_cmd():
    from autoskillit.core import CodingAgentBackend

    assert hasattr(CodingAgentBackend, "build_cmd")


def test_no_autoskillit_imports_in_protocols_backend():
    from autoskillit.core import paths

    proto_path = paths.pkg_root() / "core" / "types" / "_type_protocols_backend.py"
    source = proto_path.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from autoskillit") or stripped.startswith("import autoskillit"):
            pytest.fail(f"IL-0 violation: {stripped}")


def test_stub_class_satisfies_stream_parser():
    from autoskillit.core import SessionEvent, StreamParser

    class _Parser:
        def parse_line(self, line: str) -> SessionEvent | None:
            return None

    assert isinstance(_Parser(), StreamParser)


def test_stub_class_satisfies_coding_agent_backend():
    from autoskillit.core import (
        BackendCapabilities,
        CmdSpec,
        CodingAgentBackend,
        EnvPolicy,
        ResultParser,
        SessionLocator,
        StreamParser,
    )

    class _Backend:
        @property
        def name(self) -> str:
            return "test"

        @property
        def capabilities(self) -> BackendCapabilities: ...

        def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec: ...

        def stream_parser(self) -> StreamParser: ...

        def result_parser(self) -> ResultParser: ...

        def env_policy(self) -> EnvPolicy: ...

        def session_locator(self) -> SessionLocator: ...

    assert isinstance(_Backend(), CodingAgentBackend)
