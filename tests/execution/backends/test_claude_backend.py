from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import (
    BareResume,
    DirectInstall,
    MarketplaceInstall,
    NamedResume,
    OutputFormat,
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


class TestBuildHeadlessCmdEquivalence:
    def test_minimal_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("say hello")
        shim = commands.build_headless_cmd("say hello")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("x", model="opus")
        shim = commands.build_headless_cmd("x", model="opus")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_env_extras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("x", env_extras={"FOO": "bar"})
        shim = commands.build_headless_cmd("x", env_extras={"FOO": "bar"})
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_base_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_headless_cmd("x", base={"PATH": "/usr/bin"})
        shim = commands.build_headless_cmd("x", base={"PATH": "/usr/bin"})
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildInteractiveCmdEquivalence:
    def test_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd()
        shim = commands.build_interactive_cmd()
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_initial_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(initial_prompt="hello")
        shim = commands.build_interactive_cmd(initial_prompt="hello")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_named_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(resume_spec=NamedResume(session_id="s1"))
        shim = commands.build_interactive_cmd(resume_spec=NamedResume(session_id="s1"))
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_bare_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(resume_spec=BareResume())
        shim = commands.build_interactive_cmd(resume_spec=BareResume())
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_direct_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(plugin_source=DirectInstall(plugin_dir=Path("/pkg")))
        shim = commands.build_interactive_cmd(plugin_source=DirectInstall(plugin_dir=Path("/pkg")))
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_interactive_cmd(model="sonnet")
        shim = commands.build_interactive_cmd(model="sonnet")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildHeadlessResumeCmdEquivalence:
    def test_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_resume_cmd(resume_session_id="s1", prompt="continue")
        shim = commands.build_headless_resume_cmd(resume_session_id="s1", prompt="continue")
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_stream_json_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_resume_cmd(
            resume_session_id="s1", prompt="continue", output_format=OutputFormat.STREAM_JSON
        )
        shim = commands.build_headless_resume_cmd(
            resume_session_id="s1", prompt="continue", output_format=OutputFormat.STREAM_JSON
        )
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_direct_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
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

    def test_with_env_extras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
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
    def test_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd("/plan", **SKILL_BASE)
        shim = commands.build_skill_session_cmd("/plan", **SKILL_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        kwargs = {**SKILL_BASE, "model": "opus"}
        spec = backend.build_skill_session_cmd("/plan", **kwargs)
        shim = commands.build_skill_session_cmd("/plan", **kwargs)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_direct_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        kwargs = {**SKILL_BASE, "plugin_source": DirectInstall(plugin_dir=Path("/p"))}
        spec = backend.build_skill_session_cmd("/plan", **kwargs)
        shim = commands.build_skill_session_cmd("/plan", **kwargs)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_exit_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd(
            "/plan", exit_after_stop_delay_ms=120000, **SKILL_BASE
        )
        shim = commands.build_skill_session_cmd(
            "/plan", exit_after_stop_delay_ms=120000, **SKILL_BASE
        )
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_skill_session_cmd("/plan", resume_session_id="s1", **SKILL_BASE)
        shim = commands.build_skill_session_cmd("/plan", resume_session_id="s1", **SKILL_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


class TestBuildFoodTruckCmdEquivalence:
    def test_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(**FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(**FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(model="opus", **FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(model="opus", **FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_env_extras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(env_extras={"X": "1"}, **FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(env_extras={"X": "1"}, **FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_exit_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        spec = backend.build_food_truck_cmd(exit_after_stop_delay_ms=120000, **FOOD_TRUCK_BASE)
        shim = commands.build_food_truck_cmd(exit_after_stop_delay_ms=120000, **FOOD_TRUCK_BASE)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env

    def test_with_marketplace_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
        backend = ClaudeCodeBackend()
        kwargs = {
            **FOOD_TRUCK_BASE,
            "plugin_source": MarketplaceInstall(cache_path=Path("/cache")),
        }
        spec = backend.build_food_truck_cmd(**kwargs)
        shim = commands.build_food_truck_cmd(**kwargs)
        assert spec.cmd == tuple(shim.cmd)
        assert spec.env == shim.env


def _assert_cross_builder_equivalence(
    backend_method: str, shim_fn: str, kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    backend = ClaudeCodeBackend()
    spec = getattr(backend, backend_method)(**kwargs)
    shim = getattr(commands, shim_fn)(**kwargs)
    assert spec.cmd == tuple(shim.cmd)
    assert spec.env == shim.env


class TestCrossBuilderEquivalence:
    def test_build_headless_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _assert_cross_builder_equivalence(
            "build_headless_cmd", "build_headless_cmd", {"prompt": "x"}, monkeypatch
        )

    def test_build_interactive_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _assert_cross_builder_equivalence(
            "build_interactive_cmd", "build_interactive_cmd", {}, monkeypatch
        )

    def test_build_resume_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _assert_cross_builder_equivalence(
            "build_resume_cmd",
            "build_headless_resume_cmd",
            {"resume_session_id": "s1", "prompt": "go"},
            monkeypatch,
        )

    def test_build_skill_session_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _assert_cross_builder_equivalence(
            "build_skill_session_cmd",
            "build_skill_session_cmd",
            {"skill_command": "/plan", **SKILL_BASE},
            monkeypatch,
        )

    def test_build_food_truck_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _assert_cross_builder_equivalence(
            "build_food_truck_cmd", "build_food_truck_cmd", dict(FOOD_TRUCK_BASE), monkeypatch
        )
