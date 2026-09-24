"""Exit status and reporting for interactive cook attempts."""

from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

import autoskillit.cli.session._session_launch as _patch_session_launch
import autoskillit.execution as _patch_execution
from autoskillit.cli.session._session_process import CookAttemptResult
from autoskillit.config import AutomationConfig, ProcessTetherConfig
from autoskillit.core import FreshLaunch, PluginLoadMode
from autoskillit.core.types import TerminationReason

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _attempt_result(
    termination: TerminationReason,
    returncode: int | None,
) -> CookAttemptResult:
    return cast(
        CookAttemptResult,
        SimpleNamespace(termination=termination, returncode=returncode),
    )


@pytest.mark.parametrize(
    ("reason", "expected_fragments"),
    [
        (
            TerminationReason.IDLE_STALL,
            ("idle", "lifetime", "process_tether.cook_ceiling_seconds", "--resume"),
        ),
        (
            TerminationReason.TIMED_OUT,
            (
                "hard cap",
                "process_tether.cook_ceiling_seconds",
                "process_tether.cook_max_extension_seconds",
            ),
        ),
    ],
)
def test_lifetime_termination_uses_timeout_status_and_explains_resume(
    reason: TerminationReason,
    expected_fragments: tuple[str, ...],
) -> None:
    from autoskillit.cli.session._session_process import attempt_exit_status

    stream = io.StringIO()
    status = attempt_exit_status(
        _attempt_result(reason, -15),
        stream=stream,
    )

    assert status == 124
    message = stream.getvalue().lower()
    assert all(fragment.lower() in message for fragment in expected_fragments)


def test_external_sigkill_keeps_signal_status_and_explains_provenance() -> None:
    from autoskillit.cli.session._session_process import attempt_exit_status

    stream = io.StringIO()
    status = attempt_exit_status(
        _attempt_result(TerminationReason.NATURAL_EXIT, -9),
        stream=stream,
    )

    assert status == 137
    assert "sigkill" in stream.getvalue().lower()
    assert "outside this cook process" in stream.getvalue().lower()


@pytest.mark.parametrize("returncode", [0, 3])
def test_natural_exit_status_is_returned_without_output(returncode: int) -> None:
    from autoskillit.cli.session._session_process import attempt_exit_status

    stream = io.StringIO()
    status = attempt_exit_status(
        _attempt_result(TerminationReason.NATURAL_EXIT, returncode),
        stream=stream,
    )

    assert status == returncode
    assert stream.getvalue() == ""


def test_run_managed_cook_exits_124_even_with_reload_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.cli.session._session_cook as session_cook
    import autoskillit.cli.session._session_reload as session_reload
    from autoskillit.cli.session._session_cook import _run_managed_cook

    result = _attempt_result(TerminationReason.TIMED_OUT, -15)
    monkeypatch.setattr(
        session_cook,
        "_execute_cook_attempt",
        lambda **_kwargs: (result, "reload-session"),
    )

    def refuse_reload_admission(reload_id: str, *_args: object, **_kwargs: object) -> None:
        pytest.fail(f"lifetime termination admitted reload {reload_id!r}")

    monkeypatch.setattr(session_reload, "admit_reload", refuse_reload_admission)
    trace = SimpleNamespace(close=lambda **_kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        _run_managed_cook(
            backend=MagicMock(),
            project_dir=tmp_path,
            launch=FreshLaunch(system_prompt=""),
            launch_id="launch-id",
            config=AutomationConfig(process_tether=ProcessTetherConfig()),
            cook_env_extras={},
            managed_home=MagicMock(),
            projection_binding=MagicMock(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            trace=trace,
            trace_enabled=False,
            force_inactive_agent_teams=False,
            showed_onboarding=False,
        )

    assert exc_info.value.code == 124


def test_run_interactive_session_exits_124_before_infra_exit_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.cli.session._session_process as session_process
    from autoskillit.cli.session._session_launch import _run_interactive_session

    policy = ProcessTetherConfig()
    result = _attempt_result(TerminationReason.TIMED_OUT, -15)
    spec = SimpleNamespace(
        cmd=("claude",),
        env={},
        cwd=str(tmp_path),
        inherited_fds=(),
    )
    prepared = SimpleNamespace(spec=spec, executable=object(), pre_spawn_check=None)
    monkeypatch.setattr(
        _patch_session_launch, "_finalize_interactive_launch", lambda *_a, **_k: prepared
    )
    monkeypatch.setattr(
        _patch_session_launch, "executable_binding_matches_current_file", lambda _e: True
    )
    monkeypatch.setattr(session_process, "run_cook_attempt", lambda *_a, **_kw: result)
    monkeypatch.setattr(
        _patch_execution,
        "read_session_state",
        lambda *_args, **_kwargs: pytest.fail("infra exit classification must not run"),
    )

    @contextmanager
    def attempt_context(**_kwargs: object):
        yield SimpleNamespace(
            view_id="view-1",
            pass_fds=(),
            record_spawn=lambda *_args: None,
            record_reaped=lambda *_args: None,
            record_teardown_unproven=lambda *_args: None,
        )

    backend = SimpleNamespace(
        capabilities=SimpleNamespace(
            skill_injection_capable=False,
            cook_exact_binding_probe_required=False,
        ),
        session_attempt_context=attempt_context,
    )
    trace = SimpleNamespace(
        record_attempt_anchor=lambda **_kwargs: None,
        require_startup_budgets=lambda: None,
    )

    with pytest.raises(SystemExit) as exc_info:
        _run_interactive_session(
            launch=FreshLaunch(system_prompt=""),
            project_dir=tmp_path,
            required_env=frozenset(),
            backend=backend,
            skill_compilation=SimpleNamespace(catalog=None),
            process_tether=policy,
            managed_home=SimpleNamespace(
                generated_home=tmp_path,
                launch_id="launch-id",
                pass_fds=(),
                skills_dir=tmp_path,
            ),
            retained_projection_binding=SimpleNamespace(inherited_fds=()),
            startup_trace=trace,
            attempt=1,
        )

    assert exc_info.value.code == 124
