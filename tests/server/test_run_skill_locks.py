"""Tests for server-side ingredient lock enforcement in run_skill."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_step_mock(skip_when_false: str | None = None) -> MagicMock:
    step = MagicMock()
    step.skip_when_false = skip_when_false
    return step


class TestRunSkillDeniesLockedStep:
    """Test 6: run_skill denies locked step."""

    @pytest.mark.anyio
    async def test_run_skill_denies_locked_step(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"": {"investigate": False}},
                    "locked_ingredients": {"": {"investigate": "false"}},
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error",
                str(tmp_path),
                step_name="investigate",
                order_id="",
            )
        )

        assert result["success"] is False
        assert "INGREDIENT LOCK" in result["error"]


class TestRunSkillAllowsUnlockedStep:
    """Test 7: run_skill allows unlocked step."""

    @pytest.mark.anyio
    async def test_run_skill_allows_unlocked_step(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(json.dumps({"locked_steps": {"": {"investigate": True}}}))

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error",
                str(tmp_path),
                step_name="investigate",
                order_id="",
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")


class TestRunSkillLockCheckUsesOrderId:
    """Test 8: run_skill lock check uses order_id for pipeline scoping."""

    @pytest.mark.anyio
    async def test_run_skill_lock_check_uses_order_id_a_denied(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {
                        "a": {"investigate": False},
                        "b": {},
                    },
                    "locked_ingredients": {"a": {"investigate": "false"}},
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id="a"
            )
        )
        assert result["success"] is False
        assert "INGREDIENT LOCK" in result["error"]

    @pytest.mark.anyio
    async def test_run_skill_lock_check_uses_order_id_b_allowed(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {
                        "a": {"investigate": False},
                        "b": {},
                    },
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id="b"
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_run_skill_lock_check_unscoped_denied(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"a": {"investigate": False}},
                    "locked_ingredients": {"a": {"investigate": "false"}},
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id=""
            )
        )
        assert result["success"] is False
        assert "INGREDIENT LOCK" in result["error"]


class TestPerPipelineLockIsolation:
    """Test 17: per-pipeline lock isolation."""

    @pytest.mark.anyio
    async def test_per_pipeline_lock_isolation_allowed(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"a": {"investigate": False}},
                    "locked_ingredients": {"a": {"investigate": "false"}},
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id="b"
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_per_pipeline_lock_isolation_denied(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"a": {"investigate": False}},
                    "locked_ingredients": {"a": {"investigate": "false"}},
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id="a"
            )
        )
        assert result["success"] is False
        assert "INGREDIENT LOCK" in result["error"]


class TestRunSkillAllowsResumeOfLockedStep:
    """Test 15: run_skill allows resume of locked step."""

    @pytest.mark.anyio
    async def test_run_skill_allows_resume_of_locked_step(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"": {"investigate": False}},
                    "locked_ingredients": {"": {"investigate": "false"}},
                }
            )
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
        }

        result = json.loads(
            await run_skill(
                "/investigate error",
                str(tmp_path),
                step_name="investigate",
                order_id="",
                resume_session_id="headless-abc123",
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")
