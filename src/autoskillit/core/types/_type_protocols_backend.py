"""Backend abstraction protocol definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_backend import (
    AgentSessionResult,
    BackendCapabilities,
    BackendConventions,
    CmdSpec,
    SessionEvent,
    SkillSessionConfig,
)
from ._type_checkpoint import SessionCheckpoint
from ._type_enums import OutputFormat
from ._type_plugin_source import PluginSource
from ._type_results import ValidatedAddDir
from ._type_resume import NoResume, ResumeSpec

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

    def parse_stdout(self, stdout: str, *, exit_code: int = 0) -> AgentSessionResult: ...


@runtime_checkable
class EnvPolicy(Protocol):
    """Protocol for building the subprocess environment for a backend launch."""

    def build_env(
        self,
        base_env: Mapping[str, str],
        *,
        extras: Mapping[str, str] | None = None,
        required: frozenset[str] | None = None,
    ) -> dict[str, str]: ...


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

    Env Forwarding Contract:
        Backends with non-empty ``capabilities.mcp_env_forward_vars`` must
        ensure those vars appear in ``spec.env`` for all cmd-builders. The
        canonical injection mechanism is via ``extras`` in ``build_env()``,
        which bypasses ``AUTOSKILLIT_PRIVATE_ENV_VARS`` stripping. Enforced
        by ``tests/arch/test_mcp_env_forward_coverage.py``.
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> BackendCapabilities: ...

    @property
    def conventions(self) -> BackendConventions: ...

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec: ...

    def stream_parser(self, completion_marker: str = "") -> StreamParser: ...

    def result_parser(self) -> ResultParser: ...

    def env_policy(self) -> EnvPolicy: ...

    def session_locator(self) -> SessionLocator: ...

    def write_tool_names(self) -> frozenset[str]: ...

    def binary_name(self) -> str: ...

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_source: PluginSource | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> CmdSpec: ...

    def build_skill_session_cmd(
        self,
        skill_command: str,
        cwd: str,
        config: SkillSessionConfig,
    ) -> CmdSpec: ...

    def build_food_truck_cmd(
        self,
        *,
        orchestrator_prompt: str,
        plugin_source: PluginSource,
        cwd: str,
        completion_marker: str,
        resume_session_id: str | None = None,
        resume_checkpoint: SessionCheckpoint | None = None,
        model: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        output_format: OutputFormat = OutputFormat.STREAM_JSON,
        exit_after_stop_delay_ms: int = 0,
        stream_idle_timeout_ms: int = 0,
        scenario_step_name: str = "",
        temp_dir_relpath: str | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        sentinel_contract: str = "",
        resume_message: str | None = None,
    ) -> CmdSpec: ...

    def build_interactive_cmd(
        self,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        plugin_source: PluginSource | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        resume_spec: ResumeSpec = NoResume(),
        system_prompt: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        required_env: frozenset[str] | None = None,
        tools: Sequence[str] = (),
    ) -> CmdSpec: ...

    def validate_session_layout(self, session_dir: Path) -> list[str]: ...

    def validate_skill_content(self, content: str) -> list[str]: ...

    def version(self) -> str: ...

    def list_plugins(self) -> list[dict[str, Any]]: ...

    def ensure_pre_launch(self) -> list[str]: ...

    def translate_model(self, model: str) -> str: ...

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec: ...

    @property
    def conventions(self) -> BackendConventions: ...

    def setup_session_dir(self, session_dir: Path) -> None: ...
