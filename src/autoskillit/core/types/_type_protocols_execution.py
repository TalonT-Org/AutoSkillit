"""Execution-layer protocol definitions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_backend import CmdSpec
from ._type_checkpoint import SessionCheckpoint  # noqa: F401, TC001
from ._type_execution_identity import ExecutionIdentity
from ._type_launch import (
    BackendAuthority,
    LaunchAdapterResult,
    LaunchPreparation,
    LaunchResolutionRequest,
    ResolvedLaunchContract,
    SkillProjectionBinding,
)
from ._type_native_shell_capture import (
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
)
from ._type_plugin_source import PluginLaunchBinding
from ._type_protocols_backend import CodingAgentBackend
from ._type_protocols_workspace import PluginArtifactAuthority
from ._type_results import (
    ClosureAuthoritySpec,
    InputSpec,
    SkillResult,
    TestResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
)
from ._type_skill_contract import SkillSessionContract, StoredSkillSessionContract

__all__ = [
    "CompletionRequiredResolver",
    "InputContractResolver",
    "LaunchAdapter",
    "LaunchResolver",
    "TestRunner",
    "HeadlessExecutor",
    "SkillProjectionPreparation",
    "OutputPatternResolver",
    "RunSkillCompletionAuthority",
    "SkillContractView",
    "SkillSessionContractStore",
    "WriteExpectedResolver",
]


@runtime_checkable
class RunSkillCompletionAuthority(Protocol):
    """Server-owned lifecycle for launched ``run_skill`` invocations."""

    def admission(self, tool_name: str) -> tuple[bool, str]: ...

    def begin(
        self,
        *,
        kitchen_id: str,
        request_session_id: str,
        tracker_order_id: str,
        tracker_path: str,
        tracker_kitchen_id: str,
        tracker_incarnation_id: str,
        step_name: str,
    ) -> str: ...

    def draft(
        self,
        invocation_id: str,
        *,
        classification: str,
        success: bool,
        result_digest: str,
        child_session_id: str = "",
    ) -> Any: ...

    def publish(self, receipt_id: str) -> Any: ...

    def recover(
        self,
        *,
        kitchen_id: str,
        request_session_id: str,
    ) -> Any: ...

    def acknowledge(
        self,
        receipt_id: str,
        *,
        kitchen_id: str,
        request_session_id: str,
    ) -> Any: ...

    def apply_tracker_credit(
        self,
        *,
        tracker_order_id: str,
        tracker_path: str,
        tracker_kitchen_id: str,
        tracker_incarnation_id: str,
        step_name: str,
        effect: Callable[[], Mapping[str, Any]],
        receipt_id: str = "",
    ) -> Mapping[str, Any]: ...

    def apply_acknowledged_tracker_outcome(
        self,
        receipt_id: str,
        *,
        kitchen_id: str,
        request_session_id: str,
        effect: Callable[[], Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def clear_if_idle(self) -> bool: ...


@runtime_checkable
class LaunchAdapter(Protocol):
    """Selected backend's one-shot physical launch builder."""

    def build(self, preparation: LaunchPreparation) -> LaunchAdapterResult: ...


@runtime_checkable
class LaunchResolver(Protocol):
    """Authority selection, one-shot finalization, and secret rehydration boundary."""

    def prepare(self, request: LaunchResolutionRequest) -> LaunchPreparation: ...

    def prepare_resume(
        self,
        contract: ResolvedLaunchContract,
        *,
        command: str,
        cwd: str,
    ) -> LaunchPreparation:
        """Restore authority from persisted evidence without selecting it again."""
        ...

    def backend_for_authority(self, authority: BackendAuthority) -> CodingAgentBackend:
        """Resolve one typed authority to its registered runtime implementation."""
        ...

    def backend_for(self, preparation: LaunchPreparation) -> CodingAgentBackend:
        """Return the selected runtime adapter without repeating authority selection."""
        ...

    def finalize(
        self, preparation: LaunchPreparation, adapter: LaunchAdapter
    ) -> ResolvedLaunchContract: ...

    def validate_resume(
        self,
        expected: ResolvedLaunchContract,
        actual: ResolvedLaunchContract,
    ) -> None:
        """Reject authority or portable-semantic drift before a resumed spawn."""
        ...

    def rehydrate_secret_environment(
        self,
        contract: ResolvedLaunchContract,
        secret_environment: Mapping[str, str],
        *,
        inherited_fds: tuple[int, ...] = (),
    ) -> CmdSpec: ...


class _SkillContractOutputView(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def type(self) -> str: ...

    @property
    def allowed_values(self) -> Sequence[str]: ...


@runtime_checkable
class SkillContractView(Protocol):
    """Execution-safe shape consumed from a higher-layer skill contract."""

    @property
    def outputs(self) -> Sequence[_SkillContractOutputView]: ...

    @property
    def write_behavior(self) -> str | None: ...

    @property
    def write_expected_when(self) -> Sequence[str]: ...


@runtime_checkable
class SkillProjectionPreparation(Protocol):
    """Non-executable projection inputs awaiting one plugin binding."""

    @property
    def cwd(self) -> Path: ...

    @property
    def project_root(self) -> Path | None: ...

    @property
    def catalog(self) -> object | None: ...

    @property
    def invocation(self) -> object | None: ...

    @property
    def default_base_branch(self) -> str: ...

    def finalize(
        self,
        *,
        backend: CodingAgentBackend,
        binding: PluginLaunchBinding,
    ) -> SkillProjectionBinding: ...


@runtime_checkable
class TestRunner(Protocol):
    """Protocol for running a test suite and reporting pass/fail.

    Returns a TestResult with passed, stdout, and stderr from the test run.
    """

    def check_infrastructure(self, cwd: Path) -> str | None: ...

    async def run(self, cwd: Path) -> TestResult: ...


@runtime_checkable
class SkillSessionContractStore(Protocol):
    """Persistence boundary for skill-session contract ownership."""

    def create_provisional(
        self,
        *,
        contract: SkillSessionContract,
        snapshot: Mapping[str, str],
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
    ) -> str: ...

    def bind_launch(
        self,
        correlation_key: str,
        launch_contract: ResolvedLaunchContract,
    ) -> None: ...

    def observe_candidate(self, correlation_key: str, session_id: str) -> None: ...

    def finalize(self, correlation_key: str, session_id: str) -> None: ...

    def rebind_final_session(
        self,
        session_id: str,
        final_session_id: str,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef,
    ) -> None: ...

    def load(self, session_id: str) -> StoredSkillSessionContract: ...

    def delete(self, session_id: str) -> None: ...

    def discard(self, correlation_key: str) -> None: ...


@runtime_checkable
class HeadlessExecutor(Protocol):
    """Protocol for running headless Claude Code sessions."""

    async def run(
        self,
        skill_command: str,
        cwd: str,
        *,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        add_dirs: Sequence[ValidatedAddDir] = (),
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        expected_output_patterns: Sequence[str] = (),
        write_behavior: WriteBehaviorSpec | None = None,
        completion_marker: str = "",
        recipe_name: str = "",
        recipe_content_hash: str = "",
        recipe_composite_hash: str = "",
        recipe_version: str = "",
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        readonly_skill: bool = False,
        scope_discipline_skill: bool = False,
        completion_required: bool = False,
        write_watch_dirs: Sequence[Path] = (),
        provider_extras: Mapping[str, str] | None = None,
        profile_name: str = "",
        provider_name: str = "",
        provider_fallback_env: dict[str, str] | None = None,
        provider_fallback_name: str = "",
        resume_session_id: str = "",
        resume_launch_contract: ResolvedLaunchContract | None = None,
        resume_checkpoint: SessionCheckpoint | None = None,
        resume_message: str | None = None,
        backend_authority: BackendAuthority | None = None,
        marker_dir: Path | None = None,
        caller_session_id: str | None = None,
        inspector_eligible: bool = False,
        inspector_model: str = "",
        network_access: bool = False,
        closure_spec: ClosureAuthoritySpec | None = None,
        closure_report_root: Path | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        skill_contract: Any | None = None,
        capability_contract: SkillProjectionBinding | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
        execution_identity: ExecutionIdentity = ExecutionIdentity.empty(),
    ) -> SkillResult: ...

    async def dispatch_food_truck(
        self,
        orchestrator_prompt: str,
        cwd: str,
        *,
        completion_marker: str,
        plugin_authority: PluginArtifactAuthority | None = None,
        prior_completion_markers: Sequence[str] | None = None,
        resume_session_id: str | None = None,
        resume_checkpoint: SessionCheckpoint | None = None,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        campaign_id: str = "",
        dispatch_id: str = "",
        caller_session_id: str = "",
        project_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
        requires_packs: Sequence[str] = (),
        on_spawn: Callable[[int, int], None] | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        provider_name: str = "",
        provider_fallback_env: dict[str, str] | None = None,
        provider_fallback_name: str = "",
        profile_name: str = "",
        sentinel_contract: str = "",
        marker_dir: Path | None = None,
        session_id: str | None = None,
        resume_message: str | None = None,
        backend_authority: BackendAuthority | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        capability_preparation: SkillProjectionPreparation | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
    ) -> SkillResult: ...


@runtime_checkable
class OutputPatternResolver(Protocol):
    """Protocol for resolving expected output patterns from a skill command."""

    def __call__(self, skill_command: str) -> Sequence[str]: ...


@runtime_checkable
class WriteExpectedResolver(Protocol):
    """Protocol for resolving write-expectation metadata from skill contracts."""

    def __call__(self, skill_command: str) -> WriteBehaviorSpec: ...


@runtime_checkable
class CompletionRequiredResolver(Protocol):
    """Protocol for resolving whether a skill requires the completion marker."""

    def __call__(self, skill_command: str) -> bool: ...


@runtime_checkable
class InputContractResolver(Protocol):
    """Protocol for resolving input contract specs from skill contracts."""

    def __call__(self, skill_command: str) -> Sequence[InputSpec]: ...
