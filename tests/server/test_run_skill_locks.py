"""Tests for server-side ingredient lock enforcement in run_skill."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import FinalizedRecipeStep
from autoskillit.server.tools.tools_execution import run_skill
from tests.server._helpers import _install_active_recipe_projection

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_ACTIVE_DISPATCH = "dispatch-active"
_STALE_LOCKED_STEPS: dict[str, dict[str, bool]] = {
    "": {"investigate": False},
    "4171": {"investigate": False},
}


def _make_finalized_step(
    name: str,
    skip_when_false: str | None = None,
    *,
    skill_command: str | None = None,
) -> FinalizedRecipeStep:
    with_args = {"skill_command": skill_command} if skill_command is not None else {}
    return FinalizedRecipeStep(
        name=name,
        skip_when_false=skip_when_false,
        with_args=with_args,
    )


def _write_overlay(
    tmp_path: Path,
    locked_steps: dict[str, dict[str, bool]],
    locked_ingredients: dict[str, dict[str, str]] | None = None,
) -> None:
    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config.json").write_text("{}")
    (temp_dir / ".hook_config_overlay.json").write_text(
        json.dumps({"locked_steps": locked_steps, "locked_ingredients": locked_ingredients or {}})
    )


def test_invalid_persisted_lock_state_returns_controlled_deny(
    tool_ctx_kitchen_open, tmp_path
) -> None:
    from autoskillit.server.tools.tools_execution import _check_ingredient_locks

    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config_overlay.json").write_text("{ malformed")
    tool_ctx_kitchen_open.project_dir = tmp_path

    result = json.loads(_check_ingredient_locks("investigate", "pipeline-1") or "{}")

    assert result["success"] is False
    assert result["stage"] == "preflight:ingredient_locks"
    assert "Invalid persisted lock state" in result["error"]
    assert result["retriable"] is False


def test_lock_storage_failure_returns_retriable_deny(
    tool_ctx_kitchen_open,
    monkeypatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    monkeypatch.setattr(
        tools_execution,
        "read_overlay",
        MagicMock(side_effect=OSError("lock storage unavailable")),
    )

    result = json.loads(
        tools_execution._check_ingredient_locks("investigate", "pipeline-1") or "{}"
    )

    assert result["success"] is False
    assert result["stage"] == "preflight:ingredient_locks"
    assert "Unable to read persisted lock state" in result["error"]
    assert result["retriable"] is True


def test_wrapped_lock_storage_failure_returns_retriable_deny(
    tool_ctx_kitchen_open,
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config_overlay.json").write_text("{}")
    tool_ctx_kitchen_open.project_dir = tmp_path
    monkeypatch.setattr(
        Path,
        "read_text",
        MagicMock(side_effect=OSError("lock storage unavailable")),
    )

    result = json.loads(
        tools_execution._check_ingredient_locks("investigate", "pipeline-1") or "{}"
    )

    assert result["success"] is False
    assert result["stage"] == "preflight:ingredient_locks"
    assert "Unable to read persisted lock state" in result["error"]
    assert result["retriable"] is True


def test_unresolved_lock_check_propagates_invalid_overlay(tool_ctx_kitchen_open, tmp_path) -> None:
    from autoskillit.server.tools._overlay_state import OverlayStateError
    from autoskillit.server.tools.tools_execution import _has_active_locks

    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    (temp_dir / ".hook_config_overlay.json").write_text("{ malformed")
    tool_ctx_kitchen_open.project_dir = tmp_path

    with pytest.raises(OverlayStateError):
        _has_active_locks("pipeline-1")


def test_unresolved_lock_check_propagates_storage_failure(
    tool_ctx_kitchen_open, monkeypatch
) -> None:
    from autoskillit.server.tools import tools_execution

    monkeypatch.setattr(
        tools_execution,
        "read_overlay",
        MagicMock(side_effect=OSError("lock storage unavailable")),
    )

    with pytest.raises(OSError, match="lock storage unavailable"):
        tools_execution._has_active_locks("pipeline-1")


class TestRunSkillDeniesLockedStep:
    """Test 6: run_skill denies locked step."""

    @pytest.mark.anyio
    async def test_run_skill_denies_locked_step(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

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
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(json.dumps({"locked_steps": {"": {"investigate": True}}}))

        tool_ctx_kitchen_open.project_dir = tmp_path
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

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
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

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
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id="b"
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_run_skill_lock_check_unscoped_denied(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

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
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

        result = json.loads(
            await run_skill(
                "/investigate error", str(tmp_path), step_name="investigate", order_id="b"
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_per_pipeline_lock_isolation_denied(self, tool_ctx_kitchen_open, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

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
        temp_dir.mkdir(parents=True, exist_ok=True)
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
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

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


class TestRunSkillResolvesStepNameFromRecipe:
    """Auto-resolution of step_name from recipe when LLM omits it."""

    @pytest.mark.anyio
    async def test_run_skill_resolves_step_name_from_recipe_and_denies_locked_step(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
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

        step = _make_finalized_step(
            "investigate",
            "inputs.investigate",
            skill_command="/autoskillit:investigate ${{ inputs.target }}",
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        _install_active_recipe_projection(tool_ctx_kitchen_open, {"investigate": step})

        result = json.loads(
            await run_skill(
                "/autoskillit:investigate some-error",
                str(tmp_path),
                step_name="",
                order_id="",
            )
        )

        assert result["success"] is False
        assert "INGREDIENT LOCK" in result["error"]

    @pytest.mark.anyio
    async def test_run_skill_allows_empty_step_name_when_ambiguous_match(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"": {"assess": False}},
                    "locked_ingredients": {"": {"assess": "false"}},
                }
            )
        )

        step_a = _make_finalized_step(
            "assess",
            "inputs.assess",
            skill_command="/autoskillit:resolve-failures ...",
        )
        step_b = _make_finalized_step(
            "merge_gate_assess",
            skill_command="/autoskillit:resolve-failures ...",
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"assess": step_a, "merge_gate_assess": step_b},
        )

        result = json.loads(
            await run_skill(
                "/autoskillit:resolve-failures target",
                str(tmp_path),
                step_name="",
                order_id="",
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_run_skill_denies_unresolvable_step_name_when_locks_active(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
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

        step = _make_finalized_step(
            "investigate",
            "inputs.investigate",
            skill_command="/autoskillit:investigate ...",
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        _install_active_recipe_projection(tool_ctx_kitchen_open, {"investigate": step})

        result = json.loads(
            await run_skill(
                "/autoskillit:resolve-failures target",
                str(tmp_path),
                step_name="",
                order_id="",
            )
        )

        assert result["success"] is False
        assert "step_name is empty and could not be resolved" in result["error"]

    @pytest.mark.anyio
    async def test_run_skill_allows_empty_step_name_when_no_recipe(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.active_recipe_steps = None
        tool_ctx_kitchen_open.active_recipe_projection = None

        result = json.loads(
            await run_skill(
                "/autoskillit:investigate target",
                str(tmp_path),
                step_name="",
                order_id="",
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")

    @pytest.mark.anyio
    async def test_run_skill_allows_empty_step_name_when_no_active_denials(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"": {"investigate": True}},
                    "locked_ingredients": {},
                }
            )
        )

        step = _make_finalized_step(
            "investigate",
            "inputs.investigate",
            skill_command="/autoskillit:other-skill ...",
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        _install_active_recipe_projection(tool_ctx_kitchen_open, {"investigate": step})

        result = json.loads(
            await run_skill(
                "/autoskillit:unknown-skill target",
                str(tmp_path),
                step_name="",
                order_id="",
            )
        )
        assert "INGREDIENT LOCK" not in result.get("error", "")


class TestDispatchScopedLockIsolation:
    """The env dispatch ID scopes lock checks to the active dispatch, ignoring stale scopes."""

    @pytest.mark.parametrize(
        "own_scope",
        [{}, {_ACTIVE_DISPATCH: {}}],
        ids=["own-scope-absent", "own-scope-unlocked"],
    )
    def test_check_ingredient_locks_dispatch_env_ignores_stale_scopes(
        self, own_scope, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ) -> None:
        from autoskillit.server.tools.tools_execution import _check_ingredient_locks

        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", _ACTIVE_DISPATCH)
        _write_overlay(tmp_path, {**_STALE_LOCKED_STEPS, **own_scope})
        tool_ctx_kitchen_open.project_dir = tmp_path

        assert _check_ingredient_locks("investigate", "") is None

    def test_check_ingredient_locks_dispatch_env_enforces_own_scope(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ) -> None:
        from autoskillit.server.tools.tools_execution import _check_ingredient_locks

        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", _ACTIVE_DISPATCH)
        _write_overlay(
            tmp_path,
            {"4171": {"investigate": False}, _ACTIVE_DISPATCH: {"investigate": False}},
            {_ACTIVE_DISPATCH: {"investigate": "false"}},
        )
        tool_ctx_kitchen_open.project_dir = tmp_path

        result_str = _check_ingredient_locks("investigate", "")
        assert result_str is not None
        result = json.loads(result_str)

        assert result["success"] is False
        assert result["stage"] == "preflight:ingredient_locks"
        assert _ACTIVE_DISPATCH in result["error"]
        assert "4171" not in result["error"]

    def test_has_active_locks_dispatch_env_stale_scopes_do_not_block(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ) -> None:
        from autoskillit.server.tools.tools_execution import _has_active_locks

        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", _ACTIVE_DISPATCH)
        _write_overlay(tmp_path, _STALE_LOCKED_STEPS)
        tool_ctx_kitchen_open.project_dir = tmp_path

        assert _has_active_locks("") is False

    def test_has_active_locks_dispatch_env_own_scope_blocks(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ) -> None:
        from autoskillit.server.tools.tools_execution import _has_active_locks

        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", _ACTIVE_DISPATCH)
        _write_overlay(tmp_path, {**_STALE_LOCKED_STEPS, _ACTIVE_DISPATCH: {"investigate": False}})
        tool_ctx_kitchen_open.project_dir = tmp_path

        assert _has_active_locks("") is True

    @pytest.mark.anyio
    async def test_run_skill_dispatch_env_stale_scopes_do_not_block_first_step(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", _ACTIVE_DISPATCH)
        _write_overlay(tmp_path, _STALE_LOCKED_STEPS)
        tool_ctx_kitchen_open.project_dir = tmp_path
        _install_active_recipe_projection(
            tool_ctx_kitchen_open,
            {"investigate": _make_finalized_step("investigate", "inputs.investigate")},
        )

        result = json.loads(
            await run_skill(
                "/investigate error",
                str(tmp_path),
                step_name="investigate",
                order_id="",
            )
        )

        assert "INGREDIENT LOCK" not in result.get("error", "")
        assert result.get("stage") != "preflight:ingredient_locks"
