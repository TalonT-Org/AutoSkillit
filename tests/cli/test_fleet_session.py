"""Tests: _launch_fleet_session forwards ingredients_table to prompt builder."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest

import autoskillit.cli.prompts as _patch_cli_prompts
import autoskillit.cli.session._session_launch as _patch_session__session_launch
from autoskillit.fleet.campaign_state.state_records import ResumeDecision

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_campaign_recipe(name: str = "test-campaign") -> MagicMock:
    recipe = MagicMock()
    recipe.name = name
    recipe.dispatches = []
    recipe.continue_on_failure = False
    recipe.description = f"Test {name}"
    return recipe


@pytest.mark.parametrize("campaign_mode", [False, True], ids=("dispatch", "campaign"))
def test_fleet_call_sites_build_fresh_launches_without_managed_order_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    campaign_mode: bool,
) -> None:
    from autoskillit.cli.fleet._fleet_session import _launch_fleet_session
    from autoskillit.core import FLEET_SESSION_REQUIRED_ENV, FreshLaunch

    calls: list[tuple[object, dict[str, object]]] = []
    skill_compilation = MagicMock()
    skill_compilation.unavailability_payload = {"backend": "claude-code", "unavailable": ()}

    def capture_session(launch: object, **kwargs: object) -> None:
        calls.append((launch, kwargs))

    monkeypatch.setattr(
        _patch_session__session_launch,
        "_run_interactive_session",
        capture_session,
    )
    monkeypatch.setattr(
        _patch_cli_prompts,
        "_build_fleet_dispatch_prompt",
        lambda *args, **kwargs: "dispatch-prompt",
    )
    monkeypatch.setattr(
        _patch_cli_prompts,
        "_build_fleet_campaign_prompt",
        lambda *args, **kwargs: "campaign-prompt",
    )
    monkeypatch.setattr(
        "autoskillit.workspace.compile_session_skill_catalog",
        lambda *_args, **_kwargs: skill_compilation,
    )
    monkeypatch.chdir(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")

    if campaign_mode:
        _launch_fleet_session(
            _make_campaign_recipe(),
            "campaign-id",
            state_path,
            None,
            fleet_mode="campaign",
            initial_message="hello",
        )
    else:
        _launch_fleet_session(
            None,
            None,
            None,
            None,
            fleet_mode="dispatch",
            initial_message="hello",
        )

    assert len(calls) == 1
    launch, kwargs = calls[0]
    assert isinstance(launch, FreshLaunch)
    assert launch.system_prompt == ("campaign-prompt" if campaign_mode else "dispatch-prompt")
    assert launch.initial_prompt == "hello"
    assert kwargs["project_dir"] == tmp_path
    assert kwargs["required_env"] == FLEET_SESSION_REQUIRED_ENV
    assert kwargs["backend"] is not None
    assert kwargs["skill_compilation"] is skill_compilation
    assert "resume_spec" not in kwargs
    assert "system_prompt" not in kwargs
    assert "initial_message" not in kwargs
    extra_env = kwargs["extra_env"]
    assert isinstance(extra_env, dict)
    assert extra_env["AUTOSKILLIT_PROJECT_DIR"] == str(tmp_path)
    managed_order_inputs = {
        "managed_home",
        "plugin_binding",
        "retained_projection_binding",
        "startup_trace",
        "attempt",
    }
    assert managed_order_inputs.isdisjoint(kwargs)


def test_fleet_session_launcher_forwards_managed_join_parent_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.cli.fleet._fleet_session import _fleet_session_launcher
    from autoskillit.core import MANAGED_JOIN_PARENT_ID_ENV_VAR, FreshLaunch

    captured: dict[str, object] = {}
    backend = MagicMock()
    backend.capabilities.session_dir_persistent = False

    def capture_session(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        _patch_session__session_launch,
        "_run_interactive_session",
        capture_session,
    )

    with _fleet_session_launcher(
        backend=backend,
        project_dir=tmp_path,
        skill_compilation=MagicMock(),
        default_base_branch="main",
        workspace_temp_dir=None,
        force_inactive_agent_teams=False,
        mcp_tool_timeout_sec=1.0,
        cook_ceiling_seconds=1.0,
        systemd_scope_enabled=False,
        managed_join_parent_id="managed-join-id",
    ) as launch_session:
        launch_session(FreshLaunch(), {})

    extra_env = captured["extra_env"]
    assert isinstance(extra_env, dict)
    assert extra_env[MANAGED_JOIN_PARENT_ID_ENV_VAR] == "managed-join-id"


class TestLaunchFleetSessionIngredientsTable:
    def _call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        ingredients_table: str | None = None,
    ) -> dict:
        captured: dict = {}

        def _fake_build(
            campaign_recipe: object,
            manifest_yaml: str,
            completed_dispatches: str,
            mcp_prefix: str,
            campaign_id: str,
            **kwargs: object,
        ) -> str:
            captured["ingredients_table"] = kwargs.get("ingredients_table")
            return "fake-prompt"

        monkeypatch.setattr(_patch_cli_prompts, "_build_fleet_campaign_prompt", _fake_build)
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            lambda *a, **kw: None,
        )
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id").mkdir(parents=True)

        state_path = tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id" / "state.json"
        state_path.write_text("{}")

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
            ingredients_table=ingredients_table,
        )
        return captured

    def test_ingredients_table_forwarded_to_prompt_builder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        table = "| Name | Desc |\n| task | do it |"
        captured = self._call(monkeypatch, tmp_path, ingredients_table=table)
        assert captured["ingredients_table"] == table

    def test_ingredients_table_none_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._call(monkeypatch, tmp_path)
        assert captured["ingredients_table"] is None


class TestLaunchFleetSessionProjectDirEnv:
    """Contract test: extra_env includes AUTOSKILLIT_PROJECT_DIR for both paths."""

    def _capture_env_adhoc(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured["extra_env"] = kwargs.get("extra_env", {})
            return None

        monkeypatch.setattr(_patch_session__session_launch, "_run_interactive_session", _fake_run)
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_dispatch_prompt",
            lambda *a, **kw: "fake-prompt",
        )
        monkeypatch.chdir(tmp_path)

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(None, None, None, None, fleet_mode="dispatch")
        return captured

    def _capture_env_campaign(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured["extra_env"] = kwargs.get("extra_env", {})
            return None

        monkeypatch.setattr(_patch_session__session_launch, "_run_interactive_session", _fake_run)
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )
        monkeypatch.chdir(tmp_path)

        state_path = tmp_path / "state.json"
        state_path.write_text("{}")

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(), "test-id", state_path, None, fleet_mode="campaign"
        )
        return captured

    def test_adhoc_extra_env_contains_project_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._capture_env_adhoc(monkeypatch, tmp_path)
        assert "extra_env" in captured, (
            "_launch_fleet_session raised before _run_interactive_session was reached"
        )
        assert "AUTOSKILLIT_PROJECT_DIR" in captured["extra_env"]
        assert captured["extra_env"]["AUTOSKILLIT_PROJECT_DIR"] == str(tmp_path)

    def test_campaign_extra_env_contains_project_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._capture_env_campaign(monkeypatch, tmp_path)
        assert "extra_env" in captured, (
            "_launch_fleet_session raised before _run_interactive_session was reached"
        )
        assert "AUTOSKILLIT_PROJECT_DIR" in captured["extra_env"]
        assert captured["extra_env"]["AUTOSKILLIT_PROJECT_DIR"] == str(tmp_path)


class TestLaunchFleetSessionContinueOnFailureEnv:
    def _capture_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, continue_on_failure: bool
    ) -> dict:
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured["extra_env"] = kwargs.get("extra_env", {})
            return None

        monkeypatch.setattr(_patch_session__session_launch, "_run_interactive_session", _fake_run)
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )
        monkeypatch.chdir(tmp_path)

        state_path = tmp_path / "state.json"
        state_path.write_text("{}")

        recipe = _make_campaign_recipe()
        recipe.continue_on_failure = continue_on_failure

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(recipe, "test-id", state_path, None, fleet_mode="campaign")
        return captured

    def test_continue_on_failure_false_injects_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._capture_env(monkeypatch, tmp_path, continue_on_failure=False)
        assert captured["extra_env"]["AUTOSKILLIT_CONTINUE_ON_FAILURE"] == "false"

    def test_continue_on_failure_true_injects_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._capture_env(monkeypatch, tmp_path, continue_on_failure=True)
        assert captured["extra_env"]["AUTOSKILLIT_CONTINUE_ON_FAILURE"] == "true"


class TestReloadLoopRefreshesMetadata:
    """T3a: Reload loop calls resume_campaign_from_state to refresh metadata."""

    def _setup_common(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> Path:
        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"
        return state_path

    def test_resume_called_on_reload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """resume_campaign_from_state is called exactly once (in reload iteration)."""
        state_path = self._setup_common(monkeypatch, tmp_path)

        call_count = {"n": 0}
        call_sequence = iter(["reload-id-1", None])

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> str | None:
            return next(call_sequence)

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = "- A: success"
        fresh_meta.next_dispatch_name = "B"
        fresh_meta.is_resumable = False

        def _fake_resume(state_path_arg: Path, continue_on_failure: bool) -> MagicMock:
            call_count["n"] += 1
            return fresh_meta

        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            _fake_resume,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
        )

        assert call_count["n"] == 1

    def test_prompt_rebuilt_with_fresh_dispatches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Second prompt build uses fresh completed_dispatches from resume_campaign_from_state."""
        state_path = self._setup_common(monkeypatch, tmp_path)

        call_sequence = iter(["reload-id-1", None])

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> str | None:
            return next(call_sequence)

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = "- A: success"
        fresh_meta.next_dispatch_name = "B"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )

        prompt_calls: list[str] = []

        def _fake_build(
            campaign_recipe: object,
            manifest_yaml: str,
            completed_dispatches: str,
            mcp_prefix: str,
            campaign_id: str,
            **kwargs: object,
        ) -> str:
            prompt_calls.append(completed_dispatches)
            return "fake-prompt"

        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            _fake_build,
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
        )

        # First build uses resume_metadata=None -> "", second uses fresh_meta
        assert len(prompt_calls) == 2
        assert prompt_calls[1] == "- A: success"


class TestReloadLoopSentinelGuard:
    """T3b: FLEET_HALTED_SENTINEL in reload metadata breaks the loop cleanly."""

    def test_sentinel_on_reload_breaks_loop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When resume_campaign_from_state returns FLEET_HALTED_SENTINEL, loop exits normally."""
        from autoskillit.fleet import FLEET_HALTED_SENTINEL

        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"

        session_call_count = {"n": 0}

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> str | None:
            session_call_count["n"] += 1
            return "reload-id-sentinel"

        halted_meta = MagicMock()
        halted_meta.completed_dispatches_block = FLEET_HALTED_SENTINEL
        halted_meta.next_dispatch_name = ""
        halted_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: halted_meta,
        )
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        # Must not raise SystemExit
        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
        )

        assert session_call_count["n"] == 1


class TestReloadLoopSafetyGuards:
    """T3c: Safety guards — max reload cap and duplicate reload_id detection."""

    def test_max_reload_guard(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """SystemExit raised after _MAX_RELOADS consecutive reloads."""
        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"

        counter = {"n": 0}

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> str:
            counter["n"] += 1
            return f"unique-reload-id-{counter['n']}"

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = "- A: success"
        fresh_meta.next_dispatch_name = "B"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        with pytest.raises(SystemExit):
            _launch_fleet_session(
                _make_campaign_recipe(),
                "test-id",
                state_path,
                None,
                fleet_mode="campaign",
            )

        from autoskillit.cli.fleet._fleet_session import _MAX_RELOADS

        assert counter["n"] == _MAX_RELOADS + 1

    def test_duplicate_reload_id_aborts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """SystemExit raised when the same reload_id is returned twice."""
        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"

        # Always return the same reload_id
        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> str:
            return "repeated-reload-id"

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = "- A: success"
        fresh_meta.next_dispatch_name = "B"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        with pytest.raises(SystemExit):
            _launch_fleet_session(
                _make_campaign_recipe(),
                "test-id",
                state_path,
                None,
                fleet_mode="campaign",
            )


class TestReloadLoopUsesRestoreSession:
    """T3d: Reload loop restores the session without replaying its prompt."""

    def test_reload_uses_restore_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A reload restores its session and carries no new prompt or briefing."""
        from autoskillit.core import FreshLaunch, RestoreSession

        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"

        captured_launches: list[object] = []
        call_sequence = iter(["reload-id-abc", None])

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> str | None:
            captured_launches.append(launch)
            return next(call_sequence)

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = "- A: success"
        fresh_meta.next_dispatch_name = "B"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
        )

        assert len(captured_launches) == 2
        assert isinstance(captured_launches[0], FreshLaunch)
        restored = captured_launches[1]
        assert restored == RestoreSession(session_id="reload-id-abc")


class TestCrossInvocationResume:
    """T3e: Cross-invocation resume briefs a persisted orchestrator session."""

    def test_cross_invocation_resume_uses_briefing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A persisted session resumes with the next dispatch in its briefing."""
        from autoskillit.core import ResumeWithBriefing
        from autoskillit.fleet import DispatchRecord, write_initial_state

        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "fleet" / "test-id"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "state.json"

        dispatches = [DispatchRecord(name="dispatch-1")]
        write_initial_state(state_path, "test-id", "test-campaign", "manifest.yaml", dispatches)

        captured_launches: list[object] = []

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> None:
            captured_launches.append(launch)
            return None

        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt for dispatch-1",
        )

        fresh_meta = MagicMock(spec=ResumeDecision)
        fresh_meta.completed_dispatches_block = ""
        fresh_meta.next_dispatch_name = "dispatch-1"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session
        from autoskillit.fleet.campaign_state.state import update_orchestrator_session_id

        update_orchestrator_session_id(state_path, "prior-session-abc")

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            fresh_meta,
            fleet_mode="campaign",
        )

        assert len(captured_launches) == 1
        launch = captured_launches[0]
        assert isinstance(launch, ResumeWithBriefing)
        assert launch.session_id == "prior-session-abc"
        assert "dispatch-1" in launch.briefing
        assert not hasattr(launch, "initial_prompt")

    def test_fresh_campaign_uses_fresh_launch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A campaign without an orchestrator session starts a fresh launch."""
        from autoskillit.core import FreshLaunch
        from autoskillit.fleet import DispatchRecord, write_initial_state

        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "fleet" / "test-id"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "state.json"

        dispatches = [DispatchRecord(name="dispatch-1")]
        write_initial_state(state_path, "test-id", "test-campaign", "manifest.yaml", dispatches)

        captured_launches: list[object] = []

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ) -> None:
            captured_launches.append(launch)
            return None

        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        fresh_meta = MagicMock(spec=ResumeDecision)
        fresh_meta.completed_dispatches_block = ""
        fresh_meta.next_dispatch_name = "dispatch-1"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            fresh_meta,
            fleet_mode="campaign",
        )

        assert len(captured_launches) == 1
        launch = captured_launches[0]
        assert isinstance(launch, FreshLaunch)
        assert launch.system_prompt == "fake-prompt"
        assert launch.initial_prompt is None


@pytest.mark.parametrize("fleet_mode", ["dispatch", "campaign"])
def test_context_exhaustion_ends_fleet_session(
    fleet_mode: Literal["dispatch", "campaign"],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.session._session_launch import _InfraExitSignal
    from autoskillit.core import (
        CODEX_AUTO_COMPACTION_BLOCKED_MESSAGE,
        InfraExitCategory,
    )

    calls = 0

    def _fake_run_session(*args: object, **kwargs: object) -> _InfraExitSignal:
        nonlocal calls
        calls += 1
        return _InfraExitSignal(
            session_id="terminal-session",
            category=InfraExitCategory.CONTEXT_EXHAUSTED,
        )

    monkeypatch.setattr(
        _patch_session__session_launch,
        "_run_interactive_session",
        _fake_run_session,
    )
    monkeypatch.setattr(
        _patch_cli_prompts,
        "_build_fleet_dispatch_prompt",
        lambda *args, **kwargs: "dispatch-prompt",
    )
    monkeypatch.setattr(
        _patch_cli_prompts,
        "_build_fleet_campaign_prompt",
        lambda *args, **kwargs: "campaign-prompt",
    )
    monkeypatch.chdir(tmp_path)

    from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

    if fleet_mode == "campaign":
        state_path = tmp_path / "state.json"
        state_path.write_text("{}", encoding="utf-8")
        _launch_fleet_session(
            _make_campaign_recipe(),
            "campaign-id",
            state_path,
            None,
            fleet_mode=fleet_mode,
        )
    else:
        _launch_fleet_session(None, None, None, None, fleet_mode=fleet_mode)

    assert calls == 1
    assert capsys.readouterr().out.strip() == CODEX_AUTO_COMPACTION_BLOCKED_MESSAGE


class TestSessionIdPersistence:
    """T3f: orchestrator_session_id is persisted on infra-resume and reload."""

    def test_session_id_written_to_state_on_infra_resume(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An infra retry restores and persists its resumable session ID."""
        from autoskillit.core import InfraExitCategory, RestoreSession
        from autoskillit.fleet import DispatchRecord, read_state, write_initial_state

        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "fleet" / "test-id"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "state.json"

        dispatches = [DispatchRecord(name="dispatch-1")]
        write_initial_state(state_path, "test-id", "test-campaign", "manifest.yaml", dispatches)

        call_sequence = iter(
            [
                InfraExitCategory.API_ERROR,
                None,
            ]
        )
        captured_launches: list[object] = []

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ):
            captured_launches.append(launch)
            sig = next(call_sequence)
            if sig is None:
                return None
            from autoskillit.cli.session._session_launch import _InfraExitSignal

            return _InfraExitSignal(session_id="captured-id-xyz", category=sig)

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = ""
        fresh_meta.next_dispatch_name = "dispatch-1"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            fresh_meta,
            fleet_mode="campaign",
        )

        state = read_state(state_path)
        assert state is not None
        assert state.orchestrator_session_id == "captured-id-xyz"
        assert len(captured_launches) == 2
        restored = captured_launches[1]
        assert restored == RestoreSession(session_id="captured-id-xyz")

    def test_session_id_written_to_state_on_reload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A reload restores and persists its session ID."""
        from autoskillit.core import RestoreSession
        from autoskillit.fleet import DispatchRecord, read_state, write_initial_state

        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "fleet" / "test-id"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "state.json"

        dispatches = [DispatchRecord(name="dispatch-1")]
        write_initial_state(state_path, "test-id", "test-campaign", "manifest.yaml", dispatches)

        call_sequence = iter(["reload-id-persist-xyz", None])
        captured_launches: list[object] = []

        def _fake_run_session(
            launch: object,
            *,
            extra_env: dict,
            project_dir: Path,
            **kwargs: object,
        ):
            captured_launches.append(launch)
            return next(call_sequence)

        fresh_meta = MagicMock()
        fresh_meta.completed_dispatches_block = ""
        fresh_meta.next_dispatch_name = "dispatch-1"
        fresh_meta.is_resumable = False

        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: fresh_meta,
        )
        monkeypatch.setattr(
            _patch_cli_prompts,
            "_build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            fresh_meta,
            fleet_mode="campaign",
        )

        state = read_state(state_path)
        assert state is not None
        assert state.orchestrator_session_id == "reload-id-persist-xyz"
        assert len(captured_launches) == 2
        restored = captured_launches[1]
        assert restored == RestoreSession(session_id="reload-id-persist-xyz")


class TestFleetSessionPromptPriorDispatchId:
    async def test_build_fleet_campaign_prompt_accepts_prior_dispatch_id_parameter(
        self,
    ) -> None:
        """_build_fleet_campaign_prompt must accept prior_dispatch_id parameter."""
        from autoskillit.cli.prompts._prompts_campaign import _build_fleet_campaign_prompt

        sig = inspect.signature(_build_fleet_campaign_prompt)
        assert "prior_dispatch_id" in sig.parameters


class TestLaunchFleetSessionMaxIssuesPerFoodTruck:
    """Contract: max_issues_per_food_truck flows to campaign builder only, not dispatch builder."""

    def test_campaign_dispatch_forwards_max_issues_per_food_truck(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Campaign threads max_issues cfg to campaign prompt builder."""
        captured: dict = {}

        def _fake_build(
            campaign_recipe: object,
            manifest_yaml: str,
            completed_dispatches: str,
            mcp_prefix: str,
            campaign_id: str,
            **kwargs: object,
        ) -> str:
            captured.update(kwargs)
            return "fake-prompt"

        monkeypatch.setattr(_patch_cli_prompts, "_build_fleet_campaign_prompt", _fake_build)
        monkeypatch.setattr(
            _patch_session__session_launch,
            "_run_interactive_session",
            lambda *a, **kw: None,
        )

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "config.yaml").write_text("fleet:\n  max_issues_per_food_truck: 7\n")
        monkeypatch.chdir(tmp_path)

        state_path = tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id"
        state_path.mkdir(parents=True)
        (state_path / "state.json").write_text("{}")

        from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path / "state.json",
            None,
            fleet_mode="campaign",
        )
        assert captured.get("max_issues_per_food_truck") == 7

    def test_dispatch_prompt_does_not_accept_max_issues_per_food_truck(self) -> None:
        """_build_fleet_dispatch_prompt must not expose max_issues_per_food_truck parameter."""
        from autoskillit.cli.prompts._prompts_kitchen import _build_fleet_dispatch_prompt

        sig = inspect.signature(_build_fleet_dispatch_prompt)
        assert "max_issues_per_food_truck" not in sig.parameters
