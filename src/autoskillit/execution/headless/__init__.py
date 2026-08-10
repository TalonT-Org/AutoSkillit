"""IL-1 headless session lifecycle and food-truck dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog

from autoskillit.core import (
    SKILL_COMMAND_DISPLAY_MAX,
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    ClosureAuthoritySpec,
    ExecutionIdentity,
    LaunchResolutionRequest,
    LaunchSurface,
    LaunchValueSource,
    LaunchValueSourceKind,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureDecision,
    ProviderBinding,
    ResolvedLaunchContract,
    SemanticLaunchPlan,
    SessionCheckpoint,  # noqa: F401, TC001
    SkillProjectionBinding,
    SkillResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
    get_logger,
    temp_dir_display_str,
)
from autoskillit.execution.headless._headless_evidence import (
    _adapt_agent_result,  # noqa: F401
    _apply_budget_guard,  # noqa: F401
    _build_error_path_telemetry,  # noqa: F401
    _build_session_telemetry,  # noqa: F401
    _capture_failure,  # noqa: F401
)
from autoskillit.execution.headless._headless_execute import (
    _execute_claude_headless,
)
from autoskillit.execution.headless._headless_git import (
    _capture_git_head_sha,  # noqa: F401
    _compute_loc_changed,  # noqa: F401
    _detect_session_git_writes,  # noqa: F401
)
from autoskillit.execution.headless._headless_helpers import (
    PostSessionMetrics,
    _compute_post_session_metrics,  # noqa: F401
    _derive_step_name_from_skill_command,
    _resolve_model,  # noqa: F401
    _resolve_pty_mode,  # noqa: F401
    _resolve_session_log_dir,  # noqa: F401
    _session_log_dir,  # noqa: F401
    _stat_snapshot,  # noqa: F401
    assert_interactive_ordering,
    resolve_model_identity,
)
from autoskillit.execution.headless._headless_launch import (
    _NUDGE_TIMEOUT,  # noqa: F401
    _attempt_contract_nudge,  # noqa: F401
    _skill_launch_spec_builder,
)
from autoskillit.execution.headless._headless_outcome import validated_dispatch_cwd
from autoskillit.execution.headless._headless_path_tokens import (  # noqa: F401
    _BRANCH_NAME_PATTERN,
    _INTENTIONALLY_EXCLUDED_PATH_TOKENS,
    _OUTPUT_PATH_PATTERN,
    _OUTPUT_PATH_TOKENS,
    _RECOVERABLE_PATH_TOKENS,
    _WORKTREE_PATH_PATTERN,
    NormalizedMessages,
    _extract_branch_name,
    _extract_output_paths,
    _extract_worktree_path,
    _normalize_messages,
    _validate_output_paths,
)
from autoskillit.execution.headless._headless_recovery import (
    _ENUM_BINDING_RE,  # noqa: F401
    _TOKEN_NAME_RE,  # noqa: F401
    _extract_missing_token_hints,  # noqa: F401
    _infer_enum_token_from_write_contract,  # noqa: F401
    _is_path_capture_pattern,  # noqa: F401
    _merge_token_usage,  # noqa: F401
    _parse_single_enum_binding,  # noqa: F401
    _recover_block_from_assistant_messages,  # noqa: F401
    _recover_from_separate_marker,  # noqa: F401
    _scan_jsonl_write_paths,  # noqa: F401
    _synthesize_from_write_artifacts,  # noqa: F401
)
from autoskillit.execution.headless._headless_result import (
    _build_skill_result,  # noqa: F401
    _parse_stdout,  # noqa: F401
    _resolve_skill_session_id,  # noqa: F401
)
from autoskillit.execution.headless._managed import (
    _headless_plugin_load_mode,
    _ManagedLineageObserver,
)
from autoskillit.execution.headless._managed._food_truck_executor import (
    DefaultHeadlessExecutor,
)
from autoskillit.execution.recording import RecordingSubprocessRunner

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.recipe._contracts_types import SkillContract

__all__ = [
    "DefaultHeadlessExecutor",
    "PostSessionMetrics",
    "assert_interactive_ordering",
    "run_headless_core",
]

logger = get_logger(__name__)


async def run_headless_core(
    skill_command: str,
    cwd: str,
    ctx: ToolContext,
    *,
    model: str = "",
    step_name: str = "",
    kitchen_id: str = "",
    order_id: str = "",
    campaign_id: str = "",
    dispatch_id: str = "",
    project_dir: str = "",
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
    skill_contract: SkillContract | None = None,
    capability_contract: SkillProjectionBinding | None = None,
    native_shell_capture_decision: NativeShellCaptureDecision | None = None,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
    execution_identity: ExecutionIdentity = ExecutionIdentity(),
    on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
) -> SkillResult:
    """Shared headless runner used by run_skill.

    Does NOT check open_kitchen gate — callers in server.py are responsible.
    Accepts explicit ToolContext so this module has no server.py dependency.
    """
    cwd = validated_dispatch_cwd(
        capability_contract,
        resolved_command=skill_command,
        cwd=cwd,
    )
    cfg = ctx.config.run_skill
    effective_marker = completion_marker or cfg.completion_marker
    original_skill_command = skill_command
    if not step_name and isinstance(ctx.runner, RecordingSubprocessRunner):
        step_name = _derive_step_name_from_skill_command(skill_command)
    with structlog.contextvars.bound_contextvars(
        skill_command=original_skill_command[:SKILL_COMMAND_DISPLAY_MAX],
        step_name=step_name or None,
    ):
        model_identity = resolve_model_identity(
            model,
            ctx.config,
            step_name=step_name,
            recipe_name=recipe_name,
            profile_name=profile_name,
        )
        add_dirs_tuple = tuple(add_dirs)
        if backend_authority is None:
            if ctx.backend is None:
                raise RuntimeError("global backend authority is not configured")
            backend_authority = BackendAuthority(
                backend=ctx.backend.name,
                kind=BackendAuthorityKind.GLOBAL,
                tier=BackendAuthorityTier.GLOBAL,
                key_path="agent_backend.backend",
            )
        value_source_kind = LaunchValueSourceKind(backend_authority.kind.value)
        authority_source = LaunchValueSource(value_source_kind, backend_authority.key_path)
        default_source = LaunchValueSource(LaunchValueSourceKind.DEFAULT, "run_skill.defaults")
        provider_values = dict(provider_extras or {})
        secret_provider_keys = tuple(
            sorted(
                key
                for key in provider_values
                if any(
                    token in key.upper()
                    for token in (
                        "API_KEY",
                        "ACCESS_KEY",
                        "TOKEN",
                        "SECRET",
                        "PASSWORD",
                        "CREDENTIAL",
                    )
                )
            )
        )
        provider_binding = (
            ProviderBinding(
                provider=provider_name or profile_name or backend_authority.backend,
                profile=profile_name or "default",
                required_backend=backend_authority.backend,
                normalized_endpoint=(
                    provider_values.get("ANTHROPIC_BASE_URL")
                    or provider_values.get("OPENAI_BASE_URL")
                    or ""
                ),
                key_path="run_skill.provider",
                provider_source=authority_source,
                profile_source=authority_source,
                endpoint_source=authority_source,
                environment={},
                secret_environment_keys=secret_provider_keys,
            )
            if provider_name or profile_name or provider_values
            else None
        )
        projection_payload = (
            dict(sorted(capability_contract.projected_digests.items()))
            if capability_contract is not None
            else {"command": skill_command}
        )
        projection_digest = (
            capability_contract.projection_digest
            if capability_contract is not None
            else hashlib.sha256(
                json.dumps(projection_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        semantic_digest = (
            hashlib.sha256(
                json.dumps(
                    dict(sorted(capability_contract.semantic_digests.items())),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if capability_contract is not None
            else hashlib.sha256(skill_command.encode()).hexdigest()
        )
        launch_request = LaunchResolutionRequest(
            surface=LaunchSurface.HEADLESS_SKILL,
            authority_candidates=(backend_authority,),
            semantic_plan=SemanticLaunchPlan(
                surface=LaunchSurface.HEADLESS_SKILL,
                semantic_digest=semantic_digest,
                projection_digest=projection_digest,
            ),
            command=skill_command,
            arguments=(),
            cwd=cwd,
            requested_model=model or None,
            requested_model_source=authority_source if model else default_source,
            configured_model=model_identity.configured_model or None,
            configured_model_source=authority_source
            if model_identity.configured_model
            else default_source,
            effort=None,
            effort_source=default_source,
            sandbox_mode="pending-adapter",
            network_access=network_access,
            pty_required=False,
            inherited_fd_policy="attempt-scoped-plugin-binding",
            branch_identity={},
            worktree_identity={"cwd": cwd},
            executable_identity={"backend": backend_authority.backend},
            plugin_identity={},
            projection_identity={
                "digest": projection_digest,
                "version": str(
                    capability_contract.projection_version
                    if capability_contract is not None
                    else 0
                ),
            },
            artifact_paths=(
                capability_contract.artifact_paths if capability_contract is not None else ()
            ),
            quota_identity={"provider": provider_name or profile_name or "default"},
            provider_binding=provider_binding,
            skill_projection_binding=capability_contract,
            non_authority_metadata={"entrypoint": "headless"},
        )
        if resume_launch_contract is not None:
            if not resume_session_id:
                raise RuntimeError("persisted launch contract requires a resume session ID")
            if backend_authority != resume_launch_contract.backend_authority:
                raise RuntimeError(
                    "resume backend authority drifted from persisted launch contract"
                )
            launch_preparation = ctx.launch_resolver.prepare_resume(
                resume_launch_contract,
                command=skill_command,
                cwd=cwd,
            )
        else:
            launch_preparation = ctx.launch_resolver.prepare(launch_request)
        _cmd_backend = (
            ctx.backend
            if ctx.backend is not None and ctx.backend.name == launch_preparation.selected_backend
            else ctx.launch_resolver.backend_for(launch_preparation)
        )
        if capability_contract is not None and capability_contract.backend not in {
            None,
            _cmd_backend.name,
        }:
            raise RuntimeError("skill projection backend drifted from launch authority")
        launch_preparation = replace(
            launch_preparation,
            sandbox_mode=(
                "read-only"
                if readonly_skill
                else _cmd_backend.capabilities.default_skill_sandbox_mode
            ),
            pty_required=_resolve_pty_mode(_cmd_backend),
            executable_identity={
                "backend": _cmd_backend.name,
                "process_name": _cmd_backend.capabilities.process_name,
            },
        )
        managed_lineage_observer = _ManagedLineageObserver.create(
            store=ctx.managed_headless_session_lineage_store,
            decision=native_shell_capture_decision,
            reference=managed_lineage_ref,
            backend=_cmd_backend,
            session_kind=ManagedHeadlessSessionKind.SKILL,
        )
        plugin_load_mode = _headless_plugin_load_mode(
            _cmd_backend,
            add_dirs=add_dirs_tuple,
        )
        _build_spec = _skill_launch_spec_builder(
            backend=_cmd_backend,
            skill_command=skill_command,
            cwd=cwd,
            completion_marker=effective_marker,
            configured_model=launch_preparation.configured_model,
            output_format=cfg.output_format,
            add_dirs=add_dirs_tuple,
            exit_after_stop_delay_ms=cfg.exit_after_stop_delay_ms,
            stream_idle_timeout_ms=cfg.stream_idle_timeout_ms,
            step_name=step_name,
            temp_dir_relpath=temp_dir_display_str(ctx.config.workspace.temp_dir),
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            profile_name=profile_name,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
            readonly_skill=readonly_skill,
            scope_discipline_skill=scope_discipline_skill,
            network_access=network_access,
            native_shell_capture_decision=native_shell_capture_decision,
            managed_lineage_ref=managed_lineage_ref,
        )

        logger.debug("run_headless_core_backend_dispatch", backend=_cmd_backend.name)

        effective_timeout = timeout if timeout is not None else cfg.timeout
        effective_stale = stale_threshold if stale_threshold is not None else cfg.stale_threshold
        logger.debug(
            "run_headless_core_entry",
            cwd=cwd,
            resolved_model=model_identity.configured_model,
            timeout=effective_timeout,
            stale_threshold=effective_stale,
            plugin_load_mode=plugin_load_mode.value,
            add_dirs=list(add_dirs) if add_dirs else None,
        )
        effective_provider = provider_name or profile_name
        try:
            skill_result = await _execute_claude_headless(
                _build_spec,
                cwd,
                ctx,
                skill_command=original_skill_command,
                step_name=step_name,
                kitchen_id=kitchen_id,
                order_id=order_id,
                campaign_id=campaign_id,
                dispatch_id=dispatch_id,
                project_dir=project_dir,
                timeout=float(effective_timeout),
                stale_threshold=float(effective_stale),
                idle_output_timeout=idle_output_timeout,
                expected_output_patterns=expected_output_patterns,
                write_behavior=write_behavior,
                completion_marker=effective_marker,
                recipe_name=recipe_name,
                recipe_content_hash=recipe_content_hash,
                recipe_composite_hash=recipe_composite_hash,
                recipe_version=recipe_version,
                readonly_skill=readonly_skill,
                completion_required=completion_required,
                write_watch_dirs=write_watch_dirs,
                provider_name=effective_provider,
                plugin_authority=ctx.plugin_authority,
                plugin_load_mode=plugin_load_mode,
                provider_fallback_env=provider_fallback_env,
                provider_fallback_name=provider_fallback_name,
                provider_extras=provider_extras,
                launch_resolver=ctx.launch_resolver,
                launch_preparation=launch_preparation,
                resume_launch_contract=resume_launch_contract,
                model_identity=model_identity,
                marker_dir=marker_dir,
                session_id=caller_session_id,
                inspector_eligible=inspector_eligible,
                inspector_model=inspector_model,
                execution_identity=execution_identity,
                on_launch_resolved=on_launch_resolved,
                closure_spec=closure_spec,
                closure_report_root=closure_report_root,
                on_session_id_resolved=on_session_id_resolved,
                skill_contract=skill_contract,
                managed_lineage_observer=managed_lineage_observer,
            )
        except anyio.get_cancelled_exc_class():
            if managed_lineage_observer is not None:
                managed_lineage_observer.close(ManagedHeadlessSessionTerminalState.CANCELLED)
            raise
        except Exception:
            if managed_lineage_observer is not None:
                managed_lineage_observer.close(ManagedHeadlessSessionTerminalState.FAILED)
            raise
        if managed_lineage_observer is not None and not skill_result.needs_retry:
            managed_lineage_observer.close(
                ManagedHeadlessSessionTerminalState.SUCCEEDED
                if skill_result.success
                else ManagedHeadlessSessionTerminalState.FAILED
            )
        return skill_result
