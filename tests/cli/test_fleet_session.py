"""Tests: _launch_fleet_session forwards ingredients_table to prompt builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small, pytest.mark.feature("fleet")]


def _make_campaign_recipe(name: str = "test-campaign") -> MagicMock:
    recipe = MagicMock()
    recipe.name = name
    recipe.dispatches = []
    recipe.continue_on_failure = False
    recipe.description = f"Test {name}"
    return recipe


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

        monkeypatch.setattr("autoskillit.cli._prompts._build_fleet_campaign_prompt", _fake_build)
        monkeypatch.setattr(
            "autoskillit.cli._session_launch._run_interactive_session",
            lambda *a, **kw: None,
        )
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id").mkdir(parents=True)

        state_path = tmp_path / ".autoskillit" / "temp" / "fleet" / "test-id" / "state.json"
        state_path.write_text("{}")

        from autoskillit.cli._fleet_session import _launch_fleet_session

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


class TestLaunchFleetSessionContinueOnFailureEnv:
    def _capture_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, continue_on_failure: bool
    ) -> dict:
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured["extra_env"] = kwargs.get("extra_env", {})
            return None

        monkeypatch.setattr("autoskillit.cli._session_launch._run_interactive_session", _fake_run)
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )
        monkeypatch.chdir(tmp_path)

        state_path = tmp_path / "state.json"
        state_path.write_text("{}")

        recipe = _make_campaign_recipe()
        recipe.continue_on_failure = continue_on_failure

        from autoskillit.cli._fleet_session import _launch_fleet_session

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
            prompt: str,
            *,
            extra_env: dict,
            resume_spec: object,
            project_dir: Path,
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
            "autoskillit.cli._session_launch._run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            _fake_resume,
        )
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli._fleet_session import _launch_fleet_session

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
            prompt: str,
            *,
            extra_env: dict,
            resume_spec: object,
            project_dir: Path,
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
            "autoskillit.cli._session_launch._run_interactive_session",
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
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            _fake_build,
        )

        from autoskillit.cli._fleet_session import _launch_fleet_session

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
            prompt: str,
            *,
            extra_env: dict,
            resume_spec: object,
            project_dir: Path,
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
            "autoskillit.cli._session_launch._run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli._fleet_session import _launch_fleet_session

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
            prompt: str,
            *,
            extra_env: dict,
            resume_spec: object,
            project_dir: Path,
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
            "autoskillit.cli._session_launch._run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli._fleet_session import _launch_fleet_session

        with pytest.raises(SystemExit):
            _launch_fleet_session(
                _make_campaign_recipe(),
                "test-id",
                state_path,
                None,
                fleet_mode="campaign",
            )

        from autoskillit.cli._fleet_session import _MAX_RELOADS

        assert counter["n"] == _MAX_RELOADS + 1

    def test_duplicate_reload_id_aborts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """SystemExit raised when the same reload_id is returned twice."""
        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"

        # Always return the same reload_id
        def _fake_run_session(
            prompt: str,
            *,
            extra_env: dict,
            resume_spec: object,
            project_dir: Path,
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
            "autoskillit.cli._session_launch._run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli._fleet_session import _launch_fleet_session

        with pytest.raises(SystemExit):
            _launch_fleet_session(
                _make_campaign_recipe(),
                "test-id",
                state_path,
                None,
                fleet_mode="campaign",
            )


class TestReloadLoopUsesNamedResume:
    """T3d: Reload loop passes NoResume on first call, NamedResume on reload."""

    def test_named_resume_on_reload(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """First call uses NoResume(); after reload, NamedResume is used."""
        from autoskillit.core import NamedResume, NoResume

        monkeypatch.chdir(tmp_path)
        state_path = tmp_path / "state.json"

        captured_specs: list[object] = []
        call_sequence = iter(["reload-id-abc", None])

        def _fake_run_session(
            prompt: str,
            *,
            extra_env: dict,
            resume_spec: object,
            project_dir: Path,
        ) -> str | None:
            captured_specs.append(resume_spec)
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
            "autoskillit.cli._session_launch._run_interactive_session",
            _fake_run_session,
        )
        monkeypatch.setattr(
            "autoskillit.cli._prompts._build_fleet_campaign_prompt",
            lambda *a, **kw: "fake-prompt",
        )

        from autoskillit.cli._fleet_session import _launch_fleet_session

        _launch_fleet_session(
            _make_campaign_recipe(),
            "test-id",
            state_path,
            None,
            fleet_mode="campaign",
        )

        assert len(captured_specs) == 2
        assert captured_specs[0] == NoResume()
        assert captured_specs[1] == NamedResume(session_id="reload-id-abc")
