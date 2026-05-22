from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import (
    BareResume,
    CmdSpec,
    DirectInstall,
    MarketplaceInstall,
    NamedResume,
    OutputFormat,
    SkillSessionConfig,
)
from autoskillit.execution import commands
from autoskillit.execution.backends import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

SKILL_BASE: dict[str, Any] = {
    "cwd": "/work",
    "completion_marker": "%%DONE%%",
    "model": None,
    "plugin_source": None,
    "output_format": OutputFormat.JSON,
}

FOOD_TRUCK_BASE: dict[str, Any] = {
    "orchestrator_prompt": "dispatch the work",
    "plugin_source": DirectInstall(plugin_dir=Path("/pkg")),
    "cwd": "/work",
    "completion_marker": "%%DONE%%",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


class TestBuildHeadlessCmdEquivalence:
    def test_minimal_prompt(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("say hello")
        shim = commands.build_headless_cmd("say hello")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("x", model="opus")
        shim = commands.build_headless_cmd("x", model="opus")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_env_extras(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("x", env_extras={"FOO": "bar"})
        shim = commands.build_headless_cmd("x", env_extras={"FOO": "bar"})
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_base_env(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("x", base={"PATH": "/usr/bin"})
        shim = commands.build_headless_cmd("x", base={"PATH": "/usr/bin"})
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildInteractiveCmdEquivalence:
    def test_minimal(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd()
        shim = commands.build_interactive_cmd()
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_initial_prompt(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(initial_prompt="hello")
        shim = commands.build_interactive_cmd(initial_prompt="hello")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_named_resume(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(resume_spec=NamedResume(session_id="s1"))
        shim = commands.build_interactive_cmd(resume_spec=NamedResume(session_id="s1"))
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_bare_resume(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(resume_spec=BareResume())
        shim = commands.build_interactive_cmd(resume_spec=BareResume())
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_direct_install(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(plugin_source=DirectInstall(plugin_dir=Path("/pkg")))
        shim = commands.build_interactive_cmd(plugin_source=DirectInstall(plugin_dir=Path("/pkg")))
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(model="sonnet")
        shim = commands.build_interactive_cmd(model="sonnet")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildHeadlessResumeCmdEquivalence:
    def test_minimal(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_resume_cmd(resume_session_id="s1", prompt="continue")
        shim = commands.build_headless_resume_cmd(resume_session_id="s1", prompt="continue")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_stream_json_format(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_resume_cmd(
            resume_session_id="s1", prompt="continue", output_format=OutputFormat.STREAM_JSON
        )
        shim = commands.build_headless_resume_cmd(
            resume_session_id="s1", prompt="continue", output_format=OutputFormat.STREAM_JSON
        )
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_direct_install(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_resume_cmd(
            resume_session_id="s1",
            prompt="continue",
            plugin_source=DirectInstall(plugin_dir=Path("/pkg")),
        )
        shim = commands.build_headless_resume_cmd(
            resume_session_id="s1",
            prompt="continue",
            plugin_source=DirectInstall(plugin_dir=Path("/pkg")),
        )
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_env_extras(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_resume_cmd(
            resume_session_id="s1", prompt="continue", env_extras={"X": "1"}
        )
        shim = commands.build_headless_resume_cmd(
            resume_session_id="s1", prompt="continue", env_extras={"X": "1"}
        )
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildSkillSessionCmdEquivalence:
    def test_minimal(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd("/plan", **SKILL_BASE)
        shim = commands.build_skill_session_cmd("/plan", **SKILL_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self) -> None:
        backend = ClaudeCodeBackend()
        kwargs = {**SKILL_BASE, "model": "opus"}
        spec = backend.build_skill_session_cmd("/plan", **kwargs)
        shim = commands.build_skill_session_cmd("/plan", **kwargs)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_direct_install(self) -> None:
        backend = ClaudeCodeBackend()
        kwargs = {**SKILL_BASE, "plugin_source": DirectInstall(plugin_dir=Path("/p"))}
        spec = backend.build_skill_session_cmd("/plan", **kwargs)
        shim = commands.build_skill_session_cmd("/plan", **kwargs)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_exit_delay(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd(
            "/plan", exit_after_stop_delay_ms=120000, **SKILL_BASE
        )
        shim = commands.build_skill_session_cmd(
            "/plan", exit_after_stop_delay_ms=120000, **SKILL_BASE
        )
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_resume(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd("/plan", resume_session_id="s1", **SKILL_BASE)
        shim = commands.build_skill_session_cmd("/plan", resume_session_id="s1", **SKILL_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestResumePromptPreservation:
    """Contract: resume paths must never discard the caller's primary prompt."""

    @staticmethod
    def _extract_prompt(cmd: tuple[str, ...] | list[str]) -> str:
        cmd_list = list(cmd)
        if "-p" in cmd_list:
            return cmd_list[cmd_list.index("-p") + 1]
        return ""

    def test_food_truck_resume_preserves_orchestrator_prompt(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="Deploy service X with config Y",
            plugin_source=DirectInstall(plugin_dir=Path("/fake")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
            resume_session_id="sess-123",
            sentinel_contract="EMIT %%RESULT%%",
        )
        prompt = self._extract_prompt(spec.cmd)
        assert "Deploy service X with config Y" in prompt

    def test_food_truck_resume_includes_anti_replay_directive(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="task context",
            plugin_source=DirectInstall(plugin_dir=Path("/fake")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
            resume_session_id="sess-123",
        )
        prompt = self._extract_prompt(spec.cmd)
        assert "Do NOT re-emit" in prompt

    def test_food_truck_resume_includes_caller_resume_message(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="task context",
            plugin_source=DirectInstall(plugin_dir=Path("/fake")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
            resume_session_id="sess-123",
            resume_message="Quota guard is now disabled. Retry the blocked operation.",
        )
        prompt = self._extract_prompt(spec.cmd)
        assert "Quota guard is now disabled" in prompt

    def test_skill_session_resume_preserves_skill_command(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd(
            "/autoskillit:rectify /path/to/investigation.md",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=DirectInstall(plugin_dir=Path("/fake")),
            output_format=OutputFormat.STREAM_JSON,
            resume_session_id="sess-456",
        )
        prompt = self._extract_prompt(spec.cmd)
        assert "rectify" in prompt

    @pytest.mark.parametrize(
        "builder,kwargs,marker_text",
        [
            (
                "build_food_truck_cmd",
                dict(
                    orchestrator_prompt="UNIQUE_TASK_MARKER_12345",
                    plugin_source=DirectInstall(plugin_dir=Path("/fake")),
                    cwd="/tmp",
                    completion_marker="%%DONE%%",
                    resume_session_id="sess-inv",
                ),
                "UNIQUE_TASK_MARKER_12345",
            ),
            (
                "build_skill_session_cmd",
                dict(
                    cwd="/tmp",
                    completion_marker="%%DONE%%",
                    model=None,
                    plugin_source=DirectInstall(plugin_dir=Path("/fake")),
                    output_format=OutputFormat.STREAM_JSON,
                    resume_session_id="sess-inv",
                ),
                "UNIQUE_ARG_67890",
            ),
        ],
    )
    def test_resume_prompt_never_discards_base_prompt(
        self, builder: str, kwargs: dict[str, Any], marker_text: str
    ) -> None:
        backend = ClaudeCodeBackend()
        if builder == "build_skill_session_cmd":
            spec = backend.build_skill_session_cmd(
                f"/autoskillit:test-skill {marker_text}", **kwargs
            )
        else:
            spec = getattr(backend, builder)(**kwargs)
        prompt = self._extract_prompt(spec.cmd)
        assert marker_text in prompt


class TestBuildFoodTruckCmdEquivalence:
    def test_minimal(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(**FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(**FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(model="opus", **FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(model="opus", **FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_env_extras(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(env_extras={"X": "1"}, **FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(env_extras={"X": "1"}, **FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_exit_delay(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(exit_after_stop_delay_ms=120000, **FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(exit_after_stop_delay_ms=120000, **FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_marketplace_install(self) -> None:
        backend = ClaudeCodeBackend()
        kwargs = {
            **FOOD_TRUCK_BASE,
            "plugin_source": MarketplaceInstall(cache_path=Path("/cache")),
        }
        spec = backend.build_food_truck_cmd(**kwargs)
        shim = commands.build_food_truck_cmd(**kwargs)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildSkillSessionCmdConfigAdapter:
    def test_config_adapter_matches_impl(self) -> None:
        backend = ClaudeCodeBackend()
        config = SkillSessionConfig(
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.JSON,
        )
        via_config = backend.build_skill_session_cmd("/plan", cwd="/tmp", config=config)
        via_impl = backend._build_skill_session_cmd_impl(
            "/plan",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.JSON,
        )
        assert via_config.cmd == via_impl.cmd
        assert via_config.env == via_impl.env

    def test_config_adapter_forwards_all_fields(self) -> None:
        backend = ClaudeCodeBackend()
        config = SkillSessionConfig(
            completion_marker="%%MARKER%%",
            model="sonnet",
            plugin_source=DirectInstall(plugin_dir=Path("/p")),
            output_format=OutputFormat.STREAM_JSON,
            exit_after_stop_delay_ms=120000,
            stream_idle_timeout_ms=30000,
            scenario_step_name="step1",
            temp_dir_relpath=".autoskillit/temp",
            allowed_write_prefix="/tmp/test",
            provider_extras={"KEY": "val"},
            profile_name="my-profile",
            resume_session_id="s1",
        )
        via_config = backend.build_skill_session_cmd("/plan", cwd="/tmp", config=config)
        via_impl = backend._build_skill_session_cmd_impl(
            "/plan",
            cwd="/tmp",
            completion_marker="%%MARKER%%",
            model="sonnet",
            plugin_source=DirectInstall(plugin_dir=Path("/p")),
            output_format=OutputFormat.STREAM_JSON,
            exit_after_stop_delay_ms=120000,
            stream_idle_timeout_ms=30000,
            scenario_step_name="step1",
            temp_dir_relpath=".autoskillit/temp",
            allowed_write_prefix="/tmp/test",
            provider_extras={"KEY": "val"},
            profile_name="my-profile",
            resume_session_id="s1",
        )
        assert via_config.cmd == via_impl.cmd
        assert via_config.env == via_impl.env

    def test_legacy_flat_params_still_work(self) -> None:
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd("/plan", **SKILL_BASE)
        assert isinstance(spec, CmdSpec)
        assert any("/plan" in s or "plan" in s for s in spec.cmd)

    def test_impl_method_exists(self) -> None:
        assert hasattr(ClaudeCodeBackend, "_build_skill_session_cmd_impl")
        assert callable(getattr(ClaudeCodeBackend, "_build_skill_session_cmd_impl"))

    def test_config_path_returns_cmdspec(self) -> None:
        backend = ClaudeCodeBackend()
        config = SkillSessionConfig(completion_marker="%%DONE%%", output_format=OutputFormat.JSON)
        result = backend.build_skill_session_cmd("/plan", cwd="/tmp", config=config)
        assert isinstance(result, CmdSpec)
