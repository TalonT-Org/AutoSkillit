"""Default headless executor protocol implementation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    BackendAuthority,
    ClosureAuthoritySpec,
    ExecutionIdentity,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    ResolvedLaunchContract,
    SessionCheckpoint,
    SkillProjectionBinding,
    SkillResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
)

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.recipe._contracts_types import SkillContract


class _DefaultHeadlessExecutorBase:
    """Concrete HeadlessExecutor backed by run_headless_core."""

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx

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
        skill_contract: SkillContract | None = None,
        capability_contract: SkillProjectionBinding | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        execution_identity: ExecutionIdentity = ExecutionIdentity(),
        on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
    ) -> SkillResult:
        from autoskillit.execution.headless import run_headless_core

        cfg = self._ctx.config.run_skill
        effective_timeout = timeout if timeout is not None else cfg.timeout
        effective_stale = stale_threshold if stale_threshold is not None else cfg.stale_threshold
        return await run_headless_core(
            skill_command,
            cwd,
            self._ctx,
            model=model,
            step_name=step_name,
            kitchen_id=kitchen_id,
            order_id=order_id,
            add_dirs=add_dirs,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
            idle_output_timeout=idle_output_timeout,
            expected_output_patterns=expected_output_patterns,
            write_behavior=write_behavior,
            completion_marker=completion_marker,
            recipe_name=recipe_name,
            recipe_content_hash=recipe_content_hash,
            recipe_composite_hash=recipe_composite_hash,
            recipe_version=recipe_version,
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            readonly_skill=readonly_skill,
            completion_required=completion_required,
            write_watch_dirs=write_watch_dirs,
            provider_extras=provider_extras,
            profile_name=profile_name,
            provider_name=provider_name,
            provider_fallback_env=provider_fallback_env,
            provider_fallback_name=provider_fallback_name,
            resume_session_id=resume_session_id,
            resume_launch_contract=resume_launch_contract,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
            backend_authority=backend_authority,
            marker_dir=marker_dir,
            caller_session_id=caller_session_id,
            inspector_eligible=inspector_eligible,
            inspector_model=inspector_model,
            network_access=network_access,
            closure_spec=closure_spec,
            closure_report_root=closure_report_root,
            on_session_id_resolved=on_session_id_resolved,
            skill_contract=skill_contract,
            capability_contract=capability_contract,
            native_shell_capture_decision=native_shell_capture_decision,
            managed_lineage_ref=managed_lineage_ref,
            execution_identity=execution_identity,
            on_launch_resolved=on_launch_resolved,
        )


__all__ = ["_DefaultHeadlessExecutorBase"]
