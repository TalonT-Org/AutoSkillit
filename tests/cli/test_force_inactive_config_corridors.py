"""force_inactive_agent_teams must reach the launched spec, not just an intermediate param.

Complements tests/execution/test_force_inactive_config_reaches_builders.py, which proves
the value reaches the backend spec builders. These tests prove the CLI corridors that
call ``_run_interactive_session`` and ``build_interactive_cmd`` — the managed-launch fork
that bypasses ``prepare_interactive_launch``, ``_launch_cook_session``, and both
``_launch_fleet_session`` call sites — actually thread the caller's intent (or the
config value the caller read) all the way into the kwarg the backend receives, rather
than stopping at a parameter nobody forwards further.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.cli.session._session_launch import _launch_cook_session, _run_interactive_session
from autoskillit.core import (
    BackendCapabilities,
    BackendConventions,
    CmdSpec,
    CookSessionHandle,
    ManagedSessionHome,
    PreLaunchReadiness,
    ValidatedAddDir,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# 1. The non-probe fork of _run_interactive_session (cook_exact_binding_probe_
#    required=False — the fork Codex `order` takes). Its two direct
#    build_interactive_cmd calls bypass prepare_interactive_launch entirely.
# ---------------------------------------------------------------------------


def _make_non_probe_backend() -> tuple[object, list[dict[str, object]]]:
    """A managed-launch backend double recording every build_interactive_cmd call.

    capabilities.cook_exact_binding_probe_required defaults to False, so
    _run_interactive_session takes the direct two-call fork instead of routing
    through prepare_interactive_launch.
    """
    captured_kwargs: list[dict[str, object]] = []

    class _NonProbeBackend:
        name = "codex"
        conventions = BackendConventions()

        def binary_name(self) -> str:
            return "codex"

        @property
        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(cook_exact_binding_probe_required=False)

        def ensure_pre_launch(
            self, *, session_dir: Path | None = None, executable: object = None
        ) -> PreLaunchReadiness:
            del executable
            return PreLaunchReadiness((), {})

        def validate_interactive_invocation(self, spec: object) -> list[str]:
            del spec
            return []

        def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
            captured_kwargs.append(kwargs)
            return CmdSpec(cmd=("codex",), env={})

        def cook_session_context(
            self,
            *,
            session_home: Path,
            project_dir: Path,
            launch_id: str,
            attempt: int,
            current_resume_spec: object,
        ):
            del session_home, project_dir, current_resume_spec
            return nullcontext(
                CookSessionHandle(
                    view_id=f"{launch_id}-{attempt}",
                    pass_fds=(),
                    _record_spawn=lambda _pid, _pgid: None,
                    _record_reaped=lambda _pid, _pgid: None,
                )
            )

    return _NonProbeBackend(), captured_kwargs


def _managed_home(tmp_path: Path) -> ManagedSessionHome:
    generated_home = tmp_path / "generated"
    generated_home.mkdir()
    return ManagedSessionHome(
        launch_id="launch-id",
        generated_home=generated_home,
        skills_dir=ValidatedAddDir(str(generated_home / "add-dir")),
        pass_fds=(),
    )


def test_non_probe_fork_threads_true_intent_into_both_build_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
    _stub_interactive_prelaunch: None,
) -> None:
    """Removing the kwarg from either of the two direct build_interactive_cmd calls
    in the non-probe fork would leave that call's captured entry False here."""
    backend, captured_kwargs = _make_non_probe_backend()
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        lambda *_a, **_kw: SimpleNamespace(returncode=0),
    )

    result = _run_interactive_session(
        system_prompt="test",
        backend=backend,
        project_dir=tmp_path,
        skill_compilation=launch_kwargs["skill_compilation"],
        managed_home=_managed_home(tmp_path),
        retained_projection_binding=MagicMock(inherited_fds=()),
        startup_trace=MagicMock(),
        attempt=1,
        force_inactive_agent_teams=True,
    )

    assert result is None
    assert len(captured_kwargs) == 2, "expected both direct build_interactive_cmd calls"
    assert [kw.get("force_inactive_agent_teams") for kw in captured_kwargs] == [True, True]


def test_non_probe_fork_defaults_to_false_across_both_build_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
    _stub_interactive_prelaunch: None,
) -> None:
    """Omitting the kwarg entirely must leave both captured calls False, not absent."""
    backend, captured_kwargs = _make_non_probe_backend()
    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        lambda *_a, **_kw: SimpleNamespace(returncode=0),
    )

    result = _run_interactive_session(
        system_prompt="test",
        backend=backend,
        project_dir=tmp_path,
        skill_compilation=launch_kwargs["skill_compilation"],
        managed_home=_managed_home(tmp_path),
        retained_projection_binding=MagicMock(inherited_fds=()),
        startup_trace=MagicMock(),
        attempt=1,
    )

    assert result is None
    assert len(captured_kwargs) == 2, "expected both direct build_interactive_cmd calls"
    assert [kw.get("force_inactive_agent_teams") for kw in captured_kwargs] == [False, False]


# ---------------------------------------------------------------------------
# 2. _launch_cook_session forwards force_inactive_agent_teams to
#    _run_interactive_session.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force_inactive", [True, False])
def test_launch_cook_session_forwards_force_inactive_agent_teams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launch_kwargs: dict[str, object],
    force_inactive: bool,
) -> None:
    """Dropping the kwarg from _launch_cook_session's _run_interactive_session call
    would leave the captured value absent (None), not matching force_inactive."""
    captured: dict[str, object] = {}

    def _capture_run_interactive_session(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session",
        _capture_run_interactive_session,
    )

    _launch_cook_session(
        "system prompt",
        project_dir=tmp_path,
        required_env=frozenset(),
        force_inactive_agent_teams=force_inactive,
        **launch_kwargs,
    )

    assert captured.get("force_inactive_agent_teams") is force_inactive


# ---------------------------------------------------------------------------
# 3 & 4. Both _launch_fleet_session call sites forward
#    cfg.agent_backend.force_claude_agent_teams_inactive.
# ---------------------------------------------------------------------------


def _write_fleet_config(tmp_path: Path, *, force_inactive: bool) -> None:
    config_dir = tmp_path / ".autoskillit"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "agent_backend:\n"
        "  backend: claude-code\n"
        f"  force_claude_agent_teams_inactive: {str(force_inactive).lower()}\n"
    )


@pytest.mark.parametrize("force_inactive", [True, False])
def test_launch_fleet_session_adhoc_forwards_force_inactive_agent_teams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    force_inactive: bool,
) -> None:
    """The ad-hoc branch (~line 89) must forward the value it read off cfg.agent_backend."""
    captured: dict[str, object] = {}

    def _fake_run(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session", _fake_run
    )
    monkeypatch.setattr(
        "autoskillit.cli._prompts._build_fleet_dispatch_prompt",
        lambda *a, **kw: "dispatch-prompt",
    )
    monkeypatch.chdir(tmp_path)
    _write_fleet_config(tmp_path, force_inactive=force_inactive)

    from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

    _launch_fleet_session(None, None, None, None, fleet_mode="dispatch")

    assert captured.get("force_inactive_agent_teams") is force_inactive


@pytest.mark.parametrize("force_inactive", [True, False])
def test_launch_fleet_session_campaign_forwards_force_inactive_agent_teams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    force_inactive: bool,
) -> None:
    """The campaign branch (~line 199) must forward the same config-read value."""
    captured: dict[str, object] = {}

    def _fake_run(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session", _fake_run
    )
    monkeypatch.setattr(
        "autoskillit.cli._prompts._build_fleet_campaign_prompt",
        lambda *a, **kw: "campaign-prompt",
    )
    monkeypatch.setattr("autoskillit.cli.fleet._fleet_session.dump_yaml_str", lambda *a, **kw: "")
    monkeypatch.chdir(tmp_path)
    _write_fleet_config(tmp_path, force_inactive=force_inactive)

    from autoskillit.fleet import ResumeDecision

    monkeypatch.setattr(
        "autoskillit.fleet.resume_campaign_from_state",
        lambda *a, **kw: ResumeDecision(
            completed_dispatches_block="",
            next_dispatch_name="",
            is_resumable=False,
            dispatched_session_id="",
            retry_reason="",
        ),
    )

    from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

    recipe = MagicMock()
    recipe.dispatches = []
    recipe.continue_on_failure = False

    _launch_fleet_session(
        recipe,
        "test-campaign-id",
        tmp_path / "state.json",
        None,
        fleet_mode="campaign",
    )

    assert captured.get("force_inactive_agent_teams") is force_inactive
