"""Backend abstraction protocol definitions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ._type_backend import (
    AgentSessionResult,
    BackendCapabilities,
    BackendConventions,
    CmdSpec,
    CookSessionHandle,
    ExecutableLaunchBinding,
    SessionEvent,
    SessionSummary,
    SkillSessionConfig,
)
from ._type_checkpoint import SessionCheckpoint
from ._type_enums import ObserverStatus, OutputFormat
from ._type_execution_identity import ExecutionIdentity
from ._type_exploration import ExplorationRouterPlan
from ._type_native_shell_capture import (
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
)
from ._type_plugin_source import PluginLaunchBinding
from ._type_results import ValidatedAddDir
from ._type_resume import NoResume, ResumeSpec
from ._type_skill_contract import ExplorationVectorDef
from ._type_skill_semantics import SkillSemanticAdaptationResult, SkillSemanticPlan

__all__ = [
    "StreamParser",
    "ResultParser",
    "EnvPolicy",
    "ReadinessProbe",
    "SessionLocator",
    "ExplorationDispatchConventions",
    "ExplorationDispatchMaterialization",
    "ExplorationDispatchRenderer",
    "CodingAgentBackend",
]


@dataclass(frozen=True, slots=True)
class ExplorationDispatchConventions:
    """Backend-native call vocabulary used only after backend resolution."""

    launcher: str
    role_argument: str
    message_argument: str
    role_prefix: str = ""
    description_argument: str | None = None

    def __post_init__(self) -> None:
        values = (self.launcher, self.role_argument, self.message_argument)
        if any(not value or not value.isidentifier() for value in values):
            raise ValueError("exploration dispatch call identifiers must be valid")
        if self.description_argument is not None and (
            not self.description_argument or not self.description_argument.isidentifier()
        ):
            raise ValueError("exploration dispatch description argument must be valid")


@dataclass(frozen=True, slots=True)
class ExplorationDispatchMaterialization:
    """Native marker replacements bound to neutral and role-definition identity."""

    replacements: Mapping[str, str]
    router_plan_digest: str
    role_definition_digests: Mapping[str, str]
    preamble: str
    launch_context_ref: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.router_plan_digest:
            raise ValueError("exploration dispatch requires a router-plan digest")
        if not self.replacements:
            raise ValueError("exploration dispatch requires marker replacements")
        if set(self.replacements) != set(self.role_definition_digests):
            raise ValueError("every exploration replacement requires a role-definition digest")
        object.__setattr__(self, "replacements", MappingProxyType(dict(self.replacements)))
        object.__setattr__(
            self,
            "role_definition_digests",
            MappingProxyType(dict(self.role_definition_digests)),
        )


@runtime_checkable
class ExplorationDispatchRenderer(Protocol):
    """Backend-owned materializer for one canonical exploration router plan."""

    @property
    def conventions(self) -> ExplorationDispatchConventions: ...

    def render(
        self,
        plan: ExplorationRouterPlan,
        vectors: tuple[ExplorationVectorDef, ...],
        *,
        launch_context_ref: str | None = None,
    ) -> ExplorationDispatchMaterialization: ...


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
class ReadinessProbe(Protocol):
    """Backend-owned readiness adapter consumed by generic observers."""

    def check(self) -> ObserverStatus:
        """Perform one non-blocking readiness observation."""
        ...

    def wait(
        self,
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> ObserverStatus:
        """Wait within a bounded interval for a terminal readiness outcome."""
        ...


@runtime_checkable
class SessionLocator(Protocol):
    """Protocol for locating session log directories for a given backend."""

    def list_sessions(self, cwd: str) -> Sequence[SessionSummary]: ...

    def locate_session(self, session_id: str) -> Path | None: ...

    def project_log_dir(self, cwd: str) -> Path:
        """Return the log directory for the given project.

        ``cwd`` is a hint — implementations MAY ignore it when the backend
        uses a global session store rather than per-project directories.
        """
        ...

    def session_log_path(self, cwd: str, session_id: str) -> Path | None: ...


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

    @property
    def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer: ...

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec: ...

    def stream_parser(self, completion_marker: str = "") -> StreamParser: ...

    def result_parser(self) -> ResultParser: ...

    def env_policy(self) -> EnvPolicy: ...

    def session_locator(self) -> SessionLocator: ...

    def resolve_effective_execution_identity(
        self,
        *,
        requested: ExecutionIdentity,
        session_id: str,
    ) -> ExecutionIdentity:
        """Resolve backend-owned effective identity from authoritative session evidence."""
        ...

    def write_tool_names(self) -> frozenset[str]: ...

    def binary_name(self) -> str: ...

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_binding: PluginLaunchBinding | None = None,
        env_extras: Mapping[str, str] | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
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
        plugin_binding: PluginLaunchBinding | None,
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
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
    ) -> CmdSpec: ...

    def build_interactive_cmd(
        self,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        executable: ExecutableLaunchBinding | None = None,
        plugin_binding: PluginLaunchBinding | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        generated_home: Path | None = None,
        resume_spec: ResumeSpec = NoResume(),
        system_prompt: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        required_env: frozenset[str] | None = None,
        tools: Sequence[str] = (),
    ) -> CmdSpec: ...

    def validate_session_layout(
        self,
        session_dir: Path,
        *,
        project_dir: Path | None = None,
    ) -> list[str]: ...

    def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]: ...

    def validate_skill_content(self, content: str) -> list[str]: ...

    def adapt_skill_semantics(self, plan: SkillSemanticPlan) -> SkillSemanticAdaptationResult: ...

    def version(self) -> str: ...

    def list_plugins(self) -> list[dict[str, Any]]: ...

    def ensure_pre_launch(
        self,
        *,
        session_dir: Path | None = None,
        executable: ExecutableLaunchBinding | None = None,
        plugin_dir: Path | None = None,
    ) -> list[str]:
        """Return backend-specific launch-readiness errors.

        ``executable`` carries the shared exact launch binding. Backends whose
        readiness policy seals or probes that binding validate it here; backends
        with a different readiness boundary may intentionally ignore it.

        ``plugin_dir`` carries the session's validated generation path so that
        Codex hooks can be resolved from the exact artifact tree rather than
        performing an independent resolution.
        """
        ...

    def recover_cook_history(self) -> None: ...

    def cook_session_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
    ) -> AbstractContextManager[CookSessionHandle]: ...

    def translate_model(self, model: str) -> str: ...

    def model_config_overrides(self, model: str) -> tuple[str, ...]: ...

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec: ...

    def setup_session_dir(
        self,
        session_dir: Path,
        *,
        parent_sandbox_mode: str = "workspace-write",
        explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None: ...

    def refresh_explorer_binding_env(
        self,
        session_dir: Path,
        explorer_binding_env: Mapping[str, Mapping[str, str]],
    ) -> None: ...

    def clear_explorer_binding_env(
        self,
        session_dir: Path,
        roles: frozenset[str],
    ) -> None: ...
