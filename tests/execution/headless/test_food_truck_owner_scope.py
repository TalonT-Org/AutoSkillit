"""Tests for owner-scope wiring in DefaultHeadlessExecutor.dispatch_food_truck.

Covers the token/env handed to every physical spawn, the settle-before-admit
discipline across the first attempt and the contract nudge, the shielded final
settle on every exit path (return, propagated fault, cancellation), and the
``cleanup_incomplete`` projection from settlement completeness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import autoskillit.execution.headless._managed._food_truck_executor as _food_truck_executor
import autoskillit.execution.process._lifecycle.owner_scope as _owner_scope_module
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.process import OwnerScope, OwnerScopeSettlement, default_tether_dir
from autoskillit.execution.quota import QuotaAdmission
from tests.execution.test_headless_dispatch import _make_success_stdout, _StaticPluginAuthority
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_MARKER = "%%FT_DONE%%"


def _make_early_stop_stdout(text: str = "L3 partial output, marker withheld") -> str:
    """A SUCCESS envelope that omits the completion marker, triggering EARLY_STOP retry."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": text,
            "session_id": "ft-session",
            "is_error": False,
        }
    )


def _configure_dispatch_ctx(minimal_ctx, tmp_path: Path, runner: MockSubprocessRunner) -> None:
    minimal_ctx.runner = runner
    minimal_ctx.config.linux_tracing.log_dir = str(tmp_path)
    minimal_ctx.plugin_authority = _StaticPluginAuthority(tmp_path)
    minimal_ctx.backend = ClaudeCodeBackend()


def _install_settle_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], list[OwnerScopeSettlement]]:
    """Record every ``OwnerScope.settle_descendants`` call's seal value and settlement."""
    events: list[str] = []
    settlements: list[OwnerScopeSettlement] = []
    original_settle = OwnerScope.settle_descendants

    async def _settle_spy(self: OwnerScope, *, seal: bool) -> OwnerScopeSettlement:
        events.append(f"settle:{seal}")
        settlement = await original_settle(self, seal=seal)
        settlements.append(settlement)
        return settlement

    monkeypatch.setattr(OwnerScope, "settle_descendants", _settle_spy)
    return events, settlements


def _install_admitting_quota_fake(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    decisions: list[QuotaAdmission] | None = None,
) -> None:
    """Patch admit_quota at its dispatch_food_truck call site with a scripted fake."""
    remaining = list(decisions) if decisions is not None else None

    async def _fake_admit_quota(**_kwargs: object) -> QuotaAdmission:
        events.append("admit")
        if remaining:
            return remaining.pop(0)
        return QuotaAdmission(admitted=True, reason="test_admitted")

    monkeypatch.setattr(_food_truck_executor, "admit_quota", _fake_admit_quota)


async def _dispatch(executor, tmp_path: Path, *, dispatch_id: str):
    return await executor.dispatch_food_truck(
        "You are an L3 orchestrator",
        str(tmp_path),
        completion_marker=_MARKER,
        dispatch_id=dispatch_id,
    )


class TestOwnerScopeTokenPerDispatch:
    """The L2 launch env carries a fresh scope token for every dispatch."""

    @pytest.mark.anyio
    async def test_two_dispatches_get_distinct_tokens_and_shared_tether_dir(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import re

        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "owner-scope-log"))
        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        _configure_dispatch_ctx(minimal_ctx, tmp_path, runner)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        for _ in range(2):
            await _dispatch(executor, tmp_path, dispatch_id="fresh-token")

        assert len(runner.call_args_list) == 2
        envs = [kwargs["env"] for _, _, _, kwargs in runner.call_args_list]
        tokens = [env["AUTOSKILLIT_OWNER_SCOPE"] for env in envs]
        assert len(set(tokens)) == 2
        token_pattern = re.compile(r"^dispatch-fresh-token-[0-9a-f]{12}$")
        for token in tokens:
            assert token_pattern.fullmatch(token), token
        expected_dir = str(default_tether_dir())
        for env in envs:
            assert env["AUTOSKILLIT_OWNER_SCOPE_DIR"] == expected_dir


class TestOwnerScopeSettleDiscipline:
    """settle(seal=False) gates every physical spawn's admission; settle(seal=True) runs once."""

    @pytest.mark.anyio
    async def test_settle_precedes_admission_for_first_attempt_and_nudge(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "owner-scope-log"))
        events, settlements = _install_settle_spy(monkeypatch)
        _install_admitting_quota_fake(monkeypatch, events)

        runner = MockSubprocessRunner()
        runner.push(
            SubprocessResult(
                returncode=0,
                stdout=_make_early_stop_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        runner.push(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55556,
            )
        )
        _configure_dispatch_ctx(minimal_ctx, tmp_path, runner)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        result = await _dispatch(executor, tmp_path, dispatch_id="return-path")

        assert len(runner.call_args_list) == 2, "expected the first attempt and one nudge"
        assert events == ["settle:False", "admit", "settle:False", "admit", "settle:True"]
        assert result.success is True
        assert settlements[-1].error == ""

    @pytest.mark.anyio
    async def test_nudge_runner_fault_propagates_with_settlement_evidence_note(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.core import InfrastructureFaultError
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "owner-scope-log"))
        events, settlements = _install_settle_spy(monkeypatch)
        _install_admitting_quota_fake(monkeypatch, events)

        class _RaisingOnSecondCallRunner(MockSubprocessRunner):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            async def __call__(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise InfrastructureFaultError("nudge runner fault")
                return await super().__call__(*args, **kwargs)

        runner = _RaisingOnSecondCallRunner()
        runner.push(
            SubprocessResult(
                returncode=0,
                stdout=_make_early_stop_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        _configure_dispatch_ctx(minimal_ctx, tmp_path, runner)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        with pytest.raises(InfrastructureFaultError) as exc_info:
            await _dispatch(executor, tmp_path, dispatch_id="nudge-fault")

        assert runner.calls == 2
        assert events == ["settle:False", "admit", "settle:False", "admit", "settle:True"]
        assert settlements[-1].error == ""
        assert any("owner scope settlement evidence" in note for note in exc_info.value.__notes__)

    @pytest.mark.anyio
    async def test_cancellation_during_runner_await_still_settles_shielded(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import anyio

        from autoskillit.execution.headless import DefaultHeadlessExecutor

        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "owner-scope-log"))
        events, settlements = _install_settle_spy(monkeypatch)
        _install_admitting_quota_fake(monkeypatch, events)

        entered_runner = anyio.Event()
        hang_forever = anyio.Event()

        class _HangingRunner(MockSubprocessRunner):
            async def __call__(self, *args, **kwargs):
                entered_runner.set()
                await hang_forever.wait()
                raise AssertionError("unreachable: hang_forever is never set")

        runner = _HangingRunner()
        _configure_dispatch_ctx(minimal_ctx, tmp_path, runner)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        async def _run() -> None:
            await _dispatch(executor, tmp_path, dispatch_id="cancelled")

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run)
            await entered_runner.wait()
            tg.cancel_scope.cancel()

        assert events == ["settle:False", "admit", "settle:True"]
        assert settlements[-1].supported is True
        assert settlements[-1].error == ""

    @pytest.mark.anyio
    async def test_nudge_admission_rejection_settles_prior_attempt_before_returning(
        self, minimal_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.core.types import RetryReason, SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "owner-scope-log"))
        events, _settlements = _install_settle_spy(monkeypatch)
        _install_admitting_quota_fake(
            monkeypatch,
            events,
            decisions=[
                QuotaAdmission(admitted=True, reason="test_admitted"),
                QuotaAdmission(admitted=False, reason="quota_exhausted"),
            ],
        )

        runner = MockSubprocessRunner()
        runner.push(
            SubprocessResult(
                returncode=0,
                stdout=_make_early_stop_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        _configure_dispatch_ctx(minimal_ctx, tmp_path, runner)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        result = await _dispatch(executor, tmp_path, dispatch_id="rejected-nudge")

        # The rejected nudge admission is decided before any second runner call.
        assert len(runner.call_args_list) == 1
        # The prior attempt's settle(seal=False) is fully recorded before the nudge's
        # own settle+admission pair runs and returns the rejection.
        assert events == ["settle:False", "admit", "settle:False", "admit", "settle:True"]
        assert result.success is False
        assert result.subtype == "quota_admission_rejected"
        assert result.needs_retry is True
        assert result.retry_reason == RetryReason.RATE_LIMITED


class TestOwnerScopeCleanupIncompleteProjection:
    """settle_owner_scope's completeness projects onto SkillResult.infra.cleanup_incomplete."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("converged", "expected_cleanup_incomplete"),
        [(False, True), (True, False)],
    )
    async def test_settlement_convergence_controls_cleanup_incomplete(
        self,
        minimal_ctx,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        converged: bool,
        expected_cleanup_incomplete: bool,
    ) -> None:
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless import DefaultHeadlessExecutor

        monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "owner-scope-log"))

        def _fake_settle_owner_scope(tether_dir, token, *, seal, timeout=30.0):
            return OwnerScopeSettlement(token=token, supported=True, converged=converged)

        monkeypatch.setattr(_owner_scope_module, "settle_owner_scope", _fake_settle_owner_scope)

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=_make_success_stdout(),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        _configure_dispatch_ctx(minimal_ctx, tmp_path, runner)
        executor = DefaultHeadlessExecutor(minimal_ctx)

        result = await _dispatch(executor, tmp_path, dispatch_id="settlement-completeness")

        assert result.infra.cleanup_incomplete is expected_cleanup_incomplete
