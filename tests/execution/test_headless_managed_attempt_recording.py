"""Tests for Step 5 managed-attempt child-outcome recording (issue #4623).

Drives ``_execute_claude_headless`` end-to-end with a controllable fake
subprocess runner, following ``tests/execution/test_headless_provider_fallback.py``'s
harness style (``minimal_ctx``, monkeypatched ``_build_skill_result``, a
disabled ``LocalOtlpSink``). Covers the ``ManagedAttemptRecorder`` wiring:
no-op for an ordinary (non-managed) session, one row per physical provider
attempt across a retry, the ``new_managed_attempt_id()`` fallback when no
managed-lineage observer is present, cancellation before/after a confirmed
spawn, and binding a later-resolved backend session id onto the same row.
"""

from __future__ import annotations

from collections import deque

import anyio
import pytest

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    NativeShellCaptureMode,
    resolve_native_shell_capture_decision,
)
from autoskillit.core.types import RetryReason, SkillResult
from autoskillit.execution import child_outcomes as co
from autoskillit.execution.commands import ClaudeHeadlessCmd
from autoskillit.execution.headless._managed import _ManagedLineageObserver
from tests.execution.conftest import _launch_preparation, _mock_backend, _sink_env, _sr
from tests.fakes import FakeManagedHeadlessSessionLineageStore

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_PROVIDER_RETRY_LIMIT = 2

_STALE_RESULT = SkillResult(
    success=False,
    result="",
    session_id="s1",
    subtype="stale",
    is_error=False,
    exit_code=1,
    needs_retry=True,
    retry_reason=RetryReason.STALE,
    stderr="",
)

_BUDGET_EXHAUSTED_RESULT = SkillResult(
    success=False,
    result="",
    session_id="s1",
    subtype="budget_exhausted",
    is_error=False,
    exit_code=1,
    needs_retry=False,
    retry_reason=RetryReason.BUDGET_EXHAUSTED,
    stderr="",
)

_SUCCESS_RESULT = SkillResult(
    success=True,
    result="done",
    session_id="s2",
    subtype="success",
    is_error=False,
    exit_code=0,
    needs_retry=False,
    retry_reason=RetryReason.NONE,
    stderr="",
)


def _build_echo_spec(_binding, provider_extras):
    return ClaudeHeadlessCmd(
        cmd=("echo", "test"),
        env=dict(provider_extras or {}),
    )


def _managed_observer(tmp_path):
    """Mirrors test_headless_provider_fallback.py's ``_managed_observer`` helper."""
    anchor = tmp_path / "managed-lineage"
    anchor.mkdir()
    store = FakeManagedHeadlessSessionLineageStore()
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    lineage = store.create(
        lineage_anchor=anchor,
        launch_id="a" * 32,
        decision=decision,
        backend="codex",
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    backend = _mock_backend(channel_b_capable=False, process_name="codex")
    backend.name = "codex"
    observer = _ManagedLineageObserver.create(
        store=store,
        decision=decision,
        reference=lineage.reference,
        backend=backend,
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    assert observer is not None
    return store, observer


def _make_queued_build_result(*results: SkillResult):
    q: deque[SkillResult] = deque(results)

    def _build(*args, **kwargs):  # noqa: ARG001
        return q.popleft()

    return _build


def _runner_returning(pid: int, result, *, native_session_id: str | None = None):
    """Fake ``ctx.runner``: optionally confirms spawn/session-id, then returns ``result``."""

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        on_pid_resolved = kwargs.get("on_pid_resolved")
        if callable(on_pid_resolved):
            on_pid_resolved(pid, 0)
        if native_session_id is not None:
            on_session_id_resolved = kwargs.get("on_session_id_resolved")
            if callable(on_session_id_resolved):
                on_session_id_resolved(native_session_id)
        return result

    return fake_runner


def _runner_cancelling_without_spawn():
    """Fake ``ctx.runner``: cancels before ever confirming a spawn."""

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        raise anyio.get_cancelled_exc_class()()

    return fake_runner


def _runner_cancelling_after_spawn(pid: int):
    """Fake ``ctx.runner``: confirms spawn, then cancels before a session id is resolved."""

    async def fake_runner(cmd, **kwargs):
        on_pid_resolved = kwargs.get("on_pid_resolved")
        if callable(on_pid_resolved):
            on_pid_resolved(pid, 0)
        raise anyio.get_cancelled_exc_class()()

    return fake_runner


def _patch_headless_internals(monkeypatch, tmp_path, ctx, build_result_fn):
    """Mirrors TestProviderFallbackLoop._patch_common (test_headless_provider_fallback.py)."""
    import autoskillit.execution.headless._headless_execute as _execute_module
    import autoskillit.execution.session_log as _sl_mod
    from autoskillit.execution.headless import PostSessionMetrics

    monkeypatch.setattr(ctx.config.providers, "provider_retry_limit", _PROVIDER_RETRY_LIMIT)
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        build_result_fn,
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
        lambda *a: "",  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute.is_feature_enabled",
        lambda name, *a, **kw: name == "providers",  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute.collect_version_snapshot",
        lambda backend=None: {},
    )
    monkeypatch.setattr(_sl_mod, "flush_session_log", lambda **kw: None)  # noqa: ARG005

    class DisabledSink:
        env: dict[str, str] = {}

        @classmethod
        def start(cls, _log_dir: str) -> DisabledSink:
            return cls()

        def close(self) -> None:
            return None

        def model_evidence_for(self, _session_id: str):
            return "", ()

    monkeypatch.setattr(_execute_module, "LocalOtlpSink", DisabledSink, raising=False)


def _prepare_ctx(ctx, tmp_path):
    ctx.config.linux_tracing.log_dir = str(tmp_path / "logs")
    ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    return tmp_path / "logs"


# --- 1. child_role=None records nothing --------------------------------------


@pytest.mark.anyio
async def test_ordinary_session_records_nothing(minimal_ctx, tmp_path, monkeypatch) -> None:
    from autoskillit.execution.headless import _execute_claude_headless

    log_root = _prepare_ctx(minimal_ctx, tmp_path)
    _patch_headless_internals(
        monkeypatch, tmp_path, minimal_ctx, _make_queued_build_result(_SUCCESS_RESULT)
    )
    minimal_ctx.runner = _runner_returning(12345, _sr())

    result = await _execute_claude_headless(
        _build_echo_spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        session_id="parent-ordinary",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.success
    assert not (log_root / "child-outcomes").exists()


# --- 2. two provider attempts (retry, with a managed-lineage observer) -------


@pytest.mark.anyio
async def test_two_provider_attempts_produce_two_distinct_rows(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import _execute_claude_headless

    log_root = _prepare_ctx(minimal_ctx, tmp_path)
    _patch_headless_internals(
        monkeypatch,
        tmp_path,
        minimal_ctx,
        _make_queued_build_result(_STALE_RESULT, _SUCCESS_RESULT),
    )
    minimal_ctx.runner = _runner_returning(12345, _sr())
    sink_env = _sink_env()
    _, lineage_observer = _managed_observer(tmp_path)

    def build_spec(binding, provider_extras, managed_attempt_id):  # noqa: ARG001
        assert managed_attempt_id is not None
        return ClaudeHeadlessCmd(cmd=("echo", "test"), env=dict(provider_extras or {}))

    result = await _execute_claude_headless(
        build_spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        provider_name="minimax",
        provider_fallback_env={
            **{key: f"fallback-{key}" for key in sink_env},
            "ANTHROPIC_API_KEY": "sk-test",
        },
        provider_fallback_name="anthropic",
        managed_lineage_observer=lineage_observer,
        session_id="parent-two-attempts",
        child_role="Explore",
        child_attribution_skill="do-a",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.success
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-two-attempts", log_root=log_root
    )
    assert len(outcomes) == 2
    child_ids = {o["child_id"] for o in outcomes}
    assert len(child_ids) == 2
    by_reason = {o["terminal_reason"]: o for o in outcomes}
    assert set(by_reason) == {"unknown", "completed"}
    assert by_reason["unknown"]["raw_reason"] == "stale"
    assert by_reason["unknown"]["role"] == "Explore"
    assert by_reason["unknown"]["attribution_skill"] == "do-a"
    assert by_reason["completed"]["role"] == "Explore"


# --- 3. retry with managed lineage disabled: fallback id path ----------------


@pytest.mark.anyio
async def test_retry_without_lineage_observer_still_produces_two_distinct_rows(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import _execute_claude_headless

    log_root = _prepare_ctx(minimal_ctx, tmp_path)
    _patch_headless_internals(
        monkeypatch,
        tmp_path,
        minimal_ctx,
        _make_queued_build_result(_BUDGET_EXHAUSTED_RESULT, _SUCCESS_RESULT),
    )
    minimal_ctx.runner = _runner_returning(12345, _sr())

    result = await _execute_claude_headless(
        _build_echo_spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        provider_name="minimax",
        provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
        provider_fallback_name="anthropic",
        session_id="parent-no-observer",
        child_role="Explore",
        child_attribution_skill="do-a",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.success
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-no-observer", log_root=log_root
    )
    assert len(outcomes) == 2
    child_ids = {o["child_id"] for o in outcomes}
    assert len(child_ids) == 2
    by_reason = {o["terminal_reason"]: o for o in outcomes}
    assert set(by_reason) == {"unknown", "completed"}
    assert by_reason["unknown"]["raw_reason"] == "budget_exhausted"


# --- 4. cancellation before spawn: zero rows ----------------------------------


@pytest.mark.anyio
async def test_cancellation_before_spawn_records_nothing(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import _execute_claude_headless

    log_root = _prepare_ctx(minimal_ctx, tmp_path)
    _patch_headless_internals(
        monkeypatch, tmp_path, minimal_ctx, _make_queued_build_result(_SUCCESS_RESULT)
    )
    minimal_ctx.runner = _runner_cancelling_without_spawn()

    with pytest.raises(anyio.get_cancelled_exc_class()):
        await _execute_claude_headless(
            _build_echo_spec,
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            session_id="parent-cancel-before-spawn",
            child_role="Explore",
            child_attribution_skill="do-a",
            launch_resolver=minimal_ctx.launch_resolver,
            launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
        )

    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-cancel-before-spawn", log_root=log_root
    )
    assert outcomes == ()


# --- 5. cancellation after spawn, before a session id: one interrupted row ---


@pytest.mark.anyio
async def test_cancellation_after_spawn_records_one_interrupted_row(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import _execute_claude_headless

    log_root = _prepare_ctx(minimal_ctx, tmp_path)
    _patch_headless_internals(
        monkeypatch, tmp_path, minimal_ctx, _make_queued_build_result(_SUCCESS_RESULT)
    )
    minimal_ctx.runner = _runner_cancelling_after_spawn(12345)

    with pytest.raises(anyio.get_cancelled_exc_class()):
        await _execute_claude_headless(
            _build_echo_spec,
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            session_id="parent-cancel-after-spawn",
            child_role="Explore",
            child_attribution_skill="do-a",
            launch_resolver=minimal_ctx.launch_resolver,
            launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
        )

    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-cancel-after-spawn", log_root=log_root
    )
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == "interrupted"


# --- 6. binding a later-resolved backend id merges into the same row --------


@pytest.mark.anyio
async def test_binding_launch_alias_merges_into_same_row(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import _execute_claude_headless

    log_root = _prepare_ctx(minimal_ctx, tmp_path)
    _patch_headless_internals(
        monkeypatch, tmp_path, minimal_ctx, _make_queued_build_result(_SUCCESS_RESULT)
    )
    minimal_ctx.runner = _runner_returning(
        12345, _sr(session_id="s2"), native_session_id="backend-native-session-999"
    )

    result = await _execute_claude_headless(
        _build_echo_spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        session_id="parent-bind-alias",
        child_role="Explore",
        child_attribution_skill="do-a",
        launch_resolver=minimal_ctx.launch_resolver,
        launch_preparation=_launch_preparation(minimal_ctx, cwd=str(tmp_path)),
    )

    assert result.success
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-bind-alias", log_root=log_root
    )
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == "completed"
    assert outcomes[0]["launch_alias"] == "backend-native-session-999"
