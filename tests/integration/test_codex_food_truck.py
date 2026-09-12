from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import CmdSpec, ValidatedAddDir
from autoskillit.execution.backends.codex import CodexBackend
from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.small]


class TestCodexFoodTruckCommand:
    BASE: dict[str, object] = {
        "orchestrator_prompt": "run the plan",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "managed_skill_catalog": ValidatedAddDir(
            path="/work/add-dir",
            session_home="/work",
            skill_entries=(("test", "test/SKILL.md"),),
        ),
    }

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)

    def _build(self) -> CmdSpec:
        with plugin_binding(Path("/pkg")) as binding:
            return CodexBackend().build_food_truck_cmd(
                **self.BASE,
                plugin_binding=binding,
            )

    def test_cmd_starts_with_codex(self) -> None:
        spec = self._build()
        assert spec.cmd[0] == "codex"

    def test_app_server_subcommand_at_index_1(self) -> None:
        spec = self._build()
        assert spec.cmd[1] == "app-server"

    def test_no_json_flag(self) -> None:
        spec = self._build()
        assert "--json" not in spec.cmd

    def test_sandbox_read_only_in_plan(self) -> None:
        spec = self._build()
        assert "--sandbox" not in spec.cmd
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.sandbox == "read-only"

    def test_web_search_disabled_in_plan_config_overrides(self) -> None:
        spec = self._build()
        assert spec.app_server_plan.config_overrides["web_search"] == "disabled"
        overrides = [spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == "-c"]
        assert "web_search=disabled" not in overrides

    def test_image_generation_disabled_flag(self) -> None:
        spec = self._build()
        overrides = [spec.cmd[i + 1] for i, v in enumerate(spec.cmd[:-1]) if v == "-c"]
        assert "features.image_generation=false" in overrides

    def test_no_tools_flag(self) -> None:
        spec = self._build()
        assert "--tools" not in spec.cmd

    def test_returns_cmd_spec(self) -> None:
        result = self._build()
        assert isinstance(result, CmdSpec)
