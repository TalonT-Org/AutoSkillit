"""run_skill gate enforcement tests: headless and tier-aware."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestHeadlessGateEnforcement:
    """T_HGE: run_skill, run_cmd, run_python each return headless_error
    when the session is running with AUTOSKILLIT_HEADLESS=1 and SESSION_TYPE=skill.

    The gate is open (tool_ctx default), so _require_enabled() passes.
    _require_orchestrator_or_higher() fires first and returns subtype='headless_error'.
    """

    @pytest.fixture(autouse=True)
    def _set_headless_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")

    @pytest.mark.anyio
    async def test_run_skill_blocked_in_headless_session(self, tool_ctx):
        """run_skill returns headless_error when AUTOSKILLIT_HEADLESS=1 and SESSION_TYPE=skill."""
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result["subtype"] == "headless_error"


@pytest.mark.feature("fleet")
class TestTierAwareGateEnforcement:
    """T_TAGE: tier-aware guard permits orchestrator, denies skill and fleet as appropriate."""

    @pytest.mark.anyio
    async def test_run_skill_permitted_for_orchestrator_tier(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """run_skill does NOT return headless_error for orchestrator-tier headless sessions."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot (not a git repo)
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                returncode=0,
                stdout=json.dumps({"type": "result", "subtype": "success", "is_error": False}),
            )
        )
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result.get("cli_subtype") == "success"

    @pytest.mark.anyio
    async def test_run_skill_denied_for_skill_tier(self, tool_ctx, monkeypatch):
        """run_skill returns headless_error for skill-tier headless sessions."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result["subtype"] == "headless_error"

    @pytest.mark.anyio
    async def test_open_kitchen_denied_for_fleet_tier(self, tool_ctx, monkeypatch):
        """open_kitchen returns HeadlessDenied for fleet-tier sessions."""
        from autoskillit.server.tools.tools_kitchen import open_kitchen

        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        result = json.loads(await open_kitchen())
        assert result.get("error") == "HeadlessDenied"
        msg = result.get("user_visible_message", "").lower()
        assert "fleet" in msg

    @pytest.mark.anyio
    async def test_close_kitchen_denied_for_fleet_tier(self, tool_ctx, monkeypatch):
        """close_kitchen returns headless_error for fleet-tier sessions."""
        from autoskillit.server.tools.tools_kitchen import close_kitchen

        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        result = json.loads(await close_kitchen())
        assert result["subtype"] == "headless_error"


class TestGateErrorSchemaNormalization:
    """Gate errors use the standard 9-field response schema."""

    def test_require_enabled_gate_returns_standard_schema(self, tool_ctx):
        """Gate errors must use the same schema as normal responses."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._guards import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_result = _require_enabled()
        assert gate_result is not None
        response = json.loads(gate_result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["needs_retry"] is False
        assert "result" in response

    def test_dry_walkthrough_gate_returns_standard_schema(self, tool_ctx, tmp_path):
        """Dry-walkthrough gate errors must use the standard response schema."""
        from autoskillit.server._guards import _check_dry_walkthrough

        plan = tmp_path / "plan.md"
        plan.write_text("No marker here")
        skill_cmd = f"/implement-worktree {plan}"
        result = _check_dry_walkthrough(skill_cmd, str(tmp_path))
        assert result is not None
        response = json.loads(result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "gate_error"
