"""Managed native-shell diagnostic projection and event tests."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
import structlog.testing

from autoskillit.core import (
    CmdSpec,
    ManagedHeadlessSessionLineageStatus,
    NativeShellCaptureDiagnostic,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends import ClaudeCodeBackend
from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
from autoskillit.execution.headless._managed import _attempt as diagnostics
from autoskillit.execution.launch_resolution import DefaultLaunchResolver
from tests.execution.conftest import _launch_preparation

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _Observer:
    def __init__(self, diagnostic: NativeShellCaptureDiagnostic) -> None:
        self.diagnostic = diagnostic

    def capture_diagnostic(self) -> NativeShellCaptureDiagnostic:
        return self.diagnostic


class _FailingObserver:
    def capture_diagnostic(self) -> NativeShellCaptureDiagnostic:
        raise ValueError("invalid marker")


class _AttemptObserver:
    """Small execution seam that exposes the latest pre-bound attempt."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self.final_session_ids: list[str] = []
        self.launch_contract_digests: list[str] = []

    def allocate_attempt(self) -> str:
        attempt_id = f"{len(self.attempts) + 1:032x}"
        self.attempts.append(attempt_id)
        return attempt_id

    def capture_diagnostic(self) -> NativeShellCaptureDiagnostic:
        return dataclasses.replace(
            _diagnostic(),
            attempt_id=self.attempts[-1] if self.attempts else None,
        )

    def bind_candidate(self, _session_id: str) -> None:
        return

    def bind_launch_contract_digest(self, digest: str) -> None:
        self.launch_contract_digests.append(digest)

    def bind_returned_final(self, session_id: str) -> None:
        self.final_session_ids.append(session_id)


class _ExecutionAudit:
    def get_report(self) -> list[Any]:
        return []

    def get_report_as_dicts(self) -> list[dict[str, Any]]:
        return []


class _ExecutionTokenLog:
    def record(self, *_args: Any, **_kwargs: Any) -> None:
        return


class _ExecutionContext:
    """Execution-local context surface required by the headless core."""

    def __init__(self, *, runner: Any, log_dir: str) -> None:
        self.config = SimpleNamespace(
            run_skill=SimpleNamespace(
                idle_output_timeout=0.0,
                completion_drain_timeout=1.0,
                max_suppression_seconds=1.0,
                completion_child_deferral_ceiling_seconds=1.0,
            ),
            providers=SimpleNamespace(provider_retry_limit=0),
            linux_tracing=SimpleNamespace(
                log_dir=log_dir,
                max_sessions=20,
                proc_interval=1.0,
            ),
            features=SimpleNamespace(),
            agent_backend=SimpleNamespace(force_inactive_agent_teams=False),
        )
        self.runner = runner
        self.backend = ClaudeCodeBackend()
        self.launch_resolver = DefaultLaunchResolver()
        self.audit = _ExecutionAudit()
        self.token_log = _ExecutionTokenLog()
        self.github_api_log = None

    @staticmethod
    def build_protected_campaign_ids(_project_dir: Any) -> frozenset[str]:
        return frozenset()


def _diagnostic() -> NativeShellCaptureDiagnostic:
    return NativeShellCaptureDiagnostic(
        requested_mode=NativeShellCaptureMode.DIRECT,
        effective_mode=NativeShellCaptureMode.DIRECT,
        primary_reason=NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
        attributions=(
            NativeShellCaptureReason.LAUNCH_AUTHORIZED_DIRECT,
            NativeShellCaptureReason.PROJECT_POLICY_DISABLED,
        ),
        resolution_reason=NativeShellCaptureReason.EXPLICIT_ARGUMENT,
        lineage_status=ManagedHeadlessSessionLineageStatus.FRESH,
        launch_id="1" * 32,
        attempt_id="2" * 32,
    )


def _successful_result() -> SkillResult:
    return SkillResult(
        success=True,
        result="done",
        session_id="session-1",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def test_launch_and_exit_events_share_the_immutable_projection() -> None:
    diagnostic = _diagnostic()
    observer = cast(Any, _Observer(diagnostic))

    with structlog.testing.capture_logs() as logs:
        diagnostics.log_launch(observer)
        diagnostics.log_exit(diagnostic, _successful_result())

    launch = next(item for item in logs if item["event"] == "headless_session_launch")
    exit_event = next(item for item in logs if item["event"] == "headless_session_exit")
    assert launch["event_id"] == diagnostic.event_id(stage="launch")
    assert exit_event["event_id"] == diagnostic.event_id(stage="exit")
    assert launch["native_shell_capture"]["requested_mode"] == "direct"
    assert exit_event["native_shell_capture"]["requested_mode"] == "direct"
    assert exit_event["success"] is True


def test_invalid_observation_state_does_not_disrupt_execution() -> None:
    observer = cast(Any, _FailingObserver())
    with structlog.testing.capture_logs() as logs:
        assert diagnostics.capture(observer) is None
    assert any(item["event"] == "native_shell_capture_diagnostic_failed" for item in logs)


def test_cancelled_exit_uses_the_common_terminal_event() -> None:
    with structlog.testing.capture_logs() as logs:
        diagnostics.log_cancelled(_diagnostic())
    event = next(item for item in logs if item["event"] == "headless_session_exit")
    assert event["success"] is False
    assert event["needs_retry"] is True
    assert event["subtype"] == "cancelled"


def test_lineage_diagnostic_forces_successful_session_log_flush() -> None:
    result = SubprocessResult(
        0,
        "",
        "",
        TerminationReason.NATURAL_EXIT,
        pid=123,
    )
    assert not diagnostics.should_flush(result, _successful_result(), "", None)
    assert diagnostics.should_flush(result, _successful_result(), "", _diagnostic())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_case", "proc_snapshots"),
    [
        ("early_build_crash", None),
        ("cancellation", None),
        ("normal_success", []),
        ("no_snapshot_no_token_success", None),
    ],
)
async def test_terminal_epilogue_projects_one_attempt_aware_snapshot_to_all_sinks(
    terminal_case,
    proc_snapshots,
    tmp_path,
    monkeypatch,
) -> None:
    """Every terminal class reaches one event/summary/index projection."""

    import autoskillit.execution as execution
    import autoskillit.execution.session_log as session_log

    observer = _AttemptObserver()
    log_root = tmp_path / "logs"
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute.collect_version_snapshot",
        lambda _backend=None: {},
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
        lambda _cwd: "",
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *_args: PostSessionMetrics(0, 0, str(tmp_path)),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *_args, **_kwargs: _successful_result(),
    )

    subprocess_result = SubprocessResult(
        0,
        "",
        "",
        TerminationReason.NATURAL_EXIT,
        pid=123,
        proc_snapshots=proc_snapshots,
    )

    async def runner(_cmd, **_kwargs):
        if terminal_case == "cancellation":
            raise anyio.get_cancelled_exc_class()()
        return subprocess_result

    def build_spec(_binding, _extras, attempt_id):
        assert attempt_id == observer.attempts[-1]
        if terminal_case == "early_build_crash":
            raise RuntimeError("build failed")
        return CmdSpec(
            cmd=("echo",),
            env={
                "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE": "direct",
                "OPENAI_API_KEY": "<openai-api-key-placeholder>",
            },
        )

    flush_calls: list[dict[str, Any]] = []
    real_flush = session_log.flush_session_log

    def flush(**kwargs):
        flush_calls.append(kwargs)
        real_flush(**kwargs)

    monkeypatch.setattr(execution, "flush_session_log", flush)
    monkeypatch.setattr(session_log, "flush_session_log", flush)
    execution_ctx = cast(
        Any,
        _ExecutionContext(
            runner=runner,
            log_dir=str(log_root),
        ),
    )

    with structlog.testing.capture_logs() as logs:
        if terminal_case == "cancellation":
            with pytest.raises(anyio.get_cancelled_exc_class()):
                await _execute_claude_headless(
                    build_spec,
                    str(tmp_path),
                    execution_ctx,
                    timeout=30.0,
                    stale_threshold=5.0,
                    managed_lineage_observer=cast(Any, observer),
                    launch_resolver=execution_ctx.launch_resolver,
                    launch_preparation=_launch_preparation(
                        execution_ctx,
                        cwd=str(tmp_path),
                    ),
                )
        else:
            result = await _execute_claude_headless(
                build_spec,
                str(tmp_path),
                execution_ctx,
                timeout=30.0,
                stale_threshold=5.0,
                managed_lineage_observer=cast(Any, observer),
                launch_resolver=execution_ctx.launch_resolver,
                launch_preparation=_launch_preparation(
                    execution_ctx,
                    cwd=str(tmp_path),
                ),
            )
            assert result.success is (terminal_case.endswith("success"))

    assert len(observer.attempts) == 1
    launch = next(item for item in logs if item["event"] == "headless_session_launch")
    assert launch["native_shell_capture"]["attempt_id"] == observer.attempts[0]

    assert len(flush_calls) == 1
    diagnostic = flush_calls[0]["native_shell_capture"]
    expected_projection = diagnostic.to_dict(stage="exit")
    exit_event = next(item for item in logs if item["event"] == "headless_session_exit")
    assert exit_event["native_shell_capture"] == expected_projection

    summary_paths = list(log_root.glob("sessions/*/summary.json"))
    assert len(summary_paths) == 1
    summary = json.loads(summary_paths[0].read_text())
    index = json.loads((log_root / "sessions.jsonl").read_text().splitlines()[-1])
    assert summary["native_shell_capture"] == expected_projection
    assert index["native_shell_capture"] == expected_projection

    for sink in (launch, exit_event, summary, index):
        projection = sink["native_shell_capture"]
        assert projection["requested_mode"] == "direct"
        assert projection["effective_mode"] == "direct"
        assert projection["launch_id"] == "1" * 32
        assert projection["attempt_id"] == observer.attempts[0]
        assert projection["attributions"] == [
            "launch_authorized_direct",
            "project_policy_disabled",
        ]

    serialized_sinks = json.dumps((launch, exit_event, summary, index), sort_keys=True)
    assert "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE" not in serialized_sinks
    assert "OPENAI_API_KEY" not in serialized_sinks
    assert "<openai-api-key-placeholder>" not in serialized_sinks
