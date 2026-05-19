"""Backend abstraction protocol definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from ._type_backend import (
    AgentSessionResult,
    BackendCapabilities,
    CmdSpec,
    SessionEvent,
)
from ._type_enums import OutputFormat
from ._type_plugin_source import PluginSource

__all__ = [
    "StreamParser",
    "ResultParser",
    "EnvPolicy",
    "SessionLocator",
    "CodingAgentBackend",
]


@runtime_checkable
class StreamParser(Protocol):
    """Protocol for parsing raw backend stdout/JSONL into SessionEvent objects."""

    def parse_line(self, line: str) -> SessionEvent | None: ...


@runtime_checkable
class ResultParser(Protocol):
    """Protocol for aggregating session events into an AgentSessionResult."""

    def parse_result(
        self,
        events: Sequence[SessionEvent],
    ) -> AgentSessionResult: ...


@runtime_checkable
class EnvPolicy(Protocol):
    """Protocol for building the subprocess environment for a backend launch."""

    def build_env(self, base_env: Mapping[str, str]) -> dict[str, str]: ...


@runtime_checkable
class SessionLocator(Protocol):
    """Protocol for locating session log directories for a given backend."""

    def locate_session(self, session_id: str) -> Path | None: ...


@runtime_checkable
class CodingAgentBackend(Protocol):
    """Top-level protocol for a coding agent backend.

    Composes capabilities, command building, and the four sub-protocols
    (StreamParser, ResultParser, EnvPolicy, SessionLocator). Concrete
    implementations (e.g., ClaudeCodeBackend) satisfy this Protocol.
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> BackendCapabilities: ...

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec: ...

    def stream_parser(self) -> StreamParser: ...

    def result_parser(self) -> ResultParser: ...

    def env_policy(self) -> EnvPolicy: ...

    def session_locator(self) -> SessionLocator: ...

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_source: PluginSource | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> CmdSpec: ...
