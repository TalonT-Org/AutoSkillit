"""Tests for lock_ingredients MCP tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.server._helpers import _with_finalized_projection
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_step_mock(skip_when_false: str | None = None) -> MagicMock:
    step = MagicMock()
    step.skip_when_false = skip_when_false
    return step


class TestLockIngredientsBasic:
    """Test 1: lock_ingredients writes overlay."""

    @pytest.mark.anyio
    async def test_lock_ingredients_writes_overlay(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "investigate": _make_step_mock("inputs.investigate"),
            "build": _make_step_mock(None),
            "fix-worktree": _make_step_mock("inputs.investigate"),
        }

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")
            )

        assert result["success"] is True
        assert "locked" in result
        assert "locked_steps" in result

        overlay = temp_dir / ".hook_config_overlay.json"
        assert overlay.exists()
        data = json.loads(overlay.read_text())

        assert data["locked_ingredients"]["a"]["investigate"] == "false"
        assert data["locked_steps"]["a"]["investigate"] is False
        assert data["locked_steps"]["a"]["fix-worktree"] is False

    @pytest.mark.anyio
    async def test_lock_ingredients_writes_overlay_investigate_true(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {"investigate": _make_step_mock("inputs.investigate")}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"investigate": "true"}, pipeline_id="a")
            )

        assert result["success"] is True
        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())
        assert data["locked_steps"]["a"]["investigate"] is True


class TestLockIngredientsRejectsServerAuthoritative:
    """Test 2: lock_ingredients rejects server-authoritative ingredients."""

    @pytest.mark.anyio
    async def test_lock_ingredients_rejects_server_authoritative(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"base_branch": "develop"}, pipeline_id="a")
            )

        assert result["success"] is False
        assert "server-authoritative" in result["error"].lower()


@pytest.mark.parametrize(
    "server_auth_key",
    ["adversarial_review_level", "base_branch"],
)
@pytest.mark.anyio
async def test_lock_ingredients_envelope_has_structured_fields(server_auth_key, tmp_path):
    """Server-authoritative rejection envelope must carry user_visible_message, stage, retriable."""  # noqa: E501
    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True)
    (temp_dir / ".hook_config.json").write_text("{}")

    ctx = _make_mock_ctx()
    ctx.project_dir = tmp_path
    ctx.active_recipe_steps = {}

    with patch("autoskillit.server._get_ctx", return_value=ctx):
        from autoskillit.server.tools.tools_kitchen import lock_ingredients

        result = json.loads(
            await lock_ingredients(locked={server_auth_key: "value"}, pipeline_id="a")
        )

    assert result["success"] is False
    assert "server-authoritative" in result["error"].lower()
    assert "stage" in result
    assert isinstance(result["stage"], str)
    assert len(result["stage"]) > 0
    assert "retriable" in result
    assert result["retriable"] is False
    assert "user_visible_message" in result
    assert isinstance(result["user_visible_message"], str)
    assert len(result["user_visible_message"]) > 0


class TestLockIngredientsUnlock:
    """Test 3: lock_ingredients unlock."""

    @pytest.mark.anyio
    async def test_lock_ingredients_unlock(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {"investigate": _make_step_mock("inputs.investigate")}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            # Lock
            await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")

            # Unlock
            result = json.loads(await lock_ingredients(unlock=["investigate"], pipeline_id="a"))

        assert result["success"] is True
        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())

        assert "investigate" not in data.get("locked_ingredients", {}).get("a", {})
        assert "investigate" not in data.get("locked_steps", {}).get("a", {})


class TestLockIngredientsRequiresKitchenOpen:
    """Test 4: lock_ingredients requires kitchen open."""

    @pytest.mark.anyio
    async def test_lock_ingredients_requires_kitchen_open(self, tmp_path, monkeypatch):
        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(await lock_ingredients(locked={"investigate": "false"}))

        assert result["success"] is False
        assert "hook config file absent" in result["error"]


class TestLockIngredientsHeadlessDenial:
    """Test 5: lock_ingredients denies headless sessions."""

    @pytest.mark.anyio
    async def test_lock_ingredients_headless_denial(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            with monkeypatch.context() as m:
                m.setenv("AUTOSKILLIT_HEADLESS", "1")
                m.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
                from autoskillit.server.tools.tools_kitchen import lock_ingredients

                result = json.loads(await lock_ingredients(locked={"investigate": "false"}))

        assert result["success"] is False
        assert "skill" in result["result"].lower()


class TestUnlockRebuildsLockedSteps:
    """Test 14: unlock rebuilds locked_steps by step name, not ingredient name."""

    @pytest.mark.anyio
    async def test_unlock_rebuilds_locked_steps_by_step_name(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "fix-worktree": _make_step_mock("inputs.investigate"),
        }

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"investigate": "false"}, pipeline_id="a")
            )

        assert result["success"] is True
        assert result["locked_steps"]["fix-worktree"] is False

        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())
        assert "fix-worktree" in data["locked_steps"]["a"]

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            result2 = json.loads(await lock_ingredients(unlock=["investigate"], pipeline_id="a"))

        assert result2["success"] is True
        assert result2["locked_steps"] == {}


class TestLockIngredientsUnknownKeyValidation:
    """Tests for unknown ingredient key rejection in lock_ingredients."""

    @pytest.mark.anyio
    async def test_lock_unknown_ingredient_key_rejected(self, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "audit_impl": _make_step_mock("inputs.audit_impl"),
        }
        ctx.active_recipe_ingredients = frozenset(["audit_impl"])

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(await lock_ingredients(locked={"audit": "false"}, pipeline_id="a"))

        assert result["success"] is False
        assert "audit_impl" in result["error"]

    @pytest.mark.anyio
    async def test_lock_with_valid_ingredient_key_succeeds(self, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "audit_impl": _make_step_mock("inputs.audit_impl"),
        }
        ctx.active_recipe_ingredients = frozenset(["audit_impl"])

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"audit_impl": "false"}, pipeline_id="a")
            )

        assert result["success"] is True
        assert result["locked_steps"].get("audit_impl") is False

    @pytest.mark.anyio
    async def test_lock_suggests_close_match(self, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "audit_impl": _make_step_mock("inputs.audit_impl"),
        }
        ctx.active_recipe_ingredients = frozenset(["audit_impl"])

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(
                await lock_ingredients(locked={"auditt": "false"}, pipeline_id="a")
            )

        assert result["success"] is False
        assert "audit_impl" in str(result.get("suggestions", {}))

    @pytest.mark.anyio
    async def test_unlock_unknown_ingredient_key_rejected(self, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "audit_impl": _make_step_mock("inputs.audit_impl"),
        }
        ctx.active_recipe_ingredients = frozenset(["audit_impl"])

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            result = json.loads(await lock_ingredients(unlock=["audit"], pipeline_id="a"))

        assert result["success"] is False
        assert "audit_impl" in result["error"]

    @pytest.mark.anyio
    async def test_unlock_valid_ingredient_key_succeeds(self, tmp_path):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.active_recipe_steps = {
            "audit_impl": _make_step_mock("inputs.audit_impl"),
        }
        ctx.active_recipe_ingredients = frozenset(["audit_impl"])

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            await lock_ingredients(locked={"audit_impl": "false"}, pipeline_id="a")
            result = json.loads(await lock_ingredients(unlock=["audit_impl"], pipeline_id="a"))

        assert result["success"] is True


class TestAuthorityFeedbackConsistency:
    """Cross-tool consistency: every SERVER_AUTHORITATIVE_INGREDIENTS key must
    produce feedback on BOTH open_kitchen and lock_ingredients surfaces."""

    @pytest.mark.anyio
    async def test_every_authority_key_emits_feedback_on_both_surfaces(
        self, tmp_path, monkeypatch
    ):
        # --- open_kitchen: authority clobber warning must appear ---
        from unittest.mock import MagicMock

        from autoskillit.config.ingredient_defaults import SERVER_AUTHORITATIVE_INGREDIENTS
        from tests.server._helpers import _PATCHED_DEFAULTS
        from tests.server.conftest import _make_mock_ctx

        mock_ctx = _make_mock_ctx()
        mock_ctx.enable_components = AsyncMock()
        mock_ctx.recipes = MagicMock()
        recipe_result = _with_finalized_projection(
            {
                "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
                "valid": True,
                "suggestions": [],
                "diagram": None,
                "ingredients_table": "--- TABLE ---",
                "post_prune_step_names": ["do"],
            }
        )
        mock_ctx.recipes.load_and_validate.side_effect = lambda *_args, **_kwargs: dict(
            recipe_result
        )
        mock_recipe_info = MagicMock()
        mock_recipe_info.path = Path("/fake/recipe.yaml")
        mock_ctx.recipes.find.return_value = mock_recipe_info
        mock_recipe_obj = MagicMock()
        mock_recipe_obj.steps = {"do": MagicMock()}
        mock_recipe_obj.ingredients = {k: MagicMock() for k in SERVER_AUTHORITATIVE_INGREDIENTS}
        mock_ctx.recipes.load.return_value = mock_recipe_obj
        mock_ctx.config.migration.suppressed = []
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""

        with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
            with patch("autoskillit.server.logger"):
                with patch(
                    "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
                ):
                    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                            return_value="test-kitchen",
                        ):
                            with patch(
                                "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                                return_value=_PATCHED_DEFAULTS,
                            ):
                                from autoskillit.server.tools.tools_kitchen import open_kitchen

                                for key in sorted(SERVER_AUTHORITATIVE_INGREDIENTS):
                                    result_str = await open_kitchen(
                                        name="demo",
                                        overrides={key: "clobber_value"},
                                        ctx=mock_ctx,
                                    )
                                    parsed = json.loads(result_str)
                                    warnings = parsed.get("warnings") or []
                                    matching = [w for w in warnings if key in w]
                                    assert matching, (
                                        f"open_kitchen must emit a warning mentioning {key!r} "
                                        f"when overridden; got warnings={warnings}; "
                                        f"response={parsed}"
                                    )

        # --- lock_ingredients: structured rejection must mention each key ---
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        ctx_lk = _make_mock_ctx()
        ctx_lk.project_dir = tmp_path
        ctx_lk.active_recipe_steps = {}

        with patch("autoskillit.server._get_ctx", return_value=ctx_lk):
            from autoskillit.server.tools.tools_kitchen import lock_ingredients

            for key in sorted(SERVER_AUTHORITATIVE_INGREDIENTS):
                result = json.loads(await lock_ingredients(locked={key: "value"}, pipeline_id="a"))
                assert result["success"] is False
                assert key in result["user_visible_message"], (
                    f"lock_ingredients user_visible_message must mention {key!r}; "
                    f"got {result['user_visible_message']!r}"
                )


class TestLockIngredientsConcurrentFlock:
    """Test 16: concurrent flock."""

    @pytest.mark.anyio
    async def test_lock_ingredients_concurrent_flock(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        results: list[dict] = []
        errors: list[Exception] = []

        async def call_lock(pipeline_id: str) -> None:
            try:
                ctx = _make_mock_ctx()
                ctx.project_dir = tmp_path
                ctx.active_recipe_steps = {f"step-{pipeline_id}": _make_step_mock(None)}
                with patch("autoskillit.server._get_ctx", return_value=ctx):
                    from autoskillit.server.tools.tools_kitchen import lock_ingredients

                    result = await lock_ingredients(
                        locked={"test": "true"}, pipeline_id=pipeline_id
                    )
                    results.append(json.loads(result))
            except Exception as e:
                errors.append(e)

        import asyncio

        await asyncio.gather(
            call_lock("a"),
            call_lock("b"),
        )

        assert len(errors) == 0
        assert all(r["success"] for r in results)

        overlay = temp_dir / ".hook_config_overlay.json"
        data = json.loads(overlay.read_text())
        assert "a" in data["locked_ingredients"]
        assert "b" in data["locked_ingredients"]


# T3: REQ-ING-004
@pytest.mark.anyio
async def test_pipeline_health_lockable(tmp_path):
    """REQ-ING-004: lock_ingredients accepts pipeline_health."""
    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True)
    (temp_dir / ".hook_config.json").write_text("{}")

    ctx = _make_mock_ctx()
    ctx.project_dir = tmp_path
    ctx.active_recipe_steps = {}

    with patch("autoskillit.server._get_ctx", return_value=ctx):
        from autoskillit.server.tools.tools_kitchen import lock_ingredients

        result_str = await lock_ingredients(locked={"pipeline_health": "true"}, pipeline_id="a")

    result = json.loads(result_str)
    assert result.get("success") is True, f"pipeline_health must be lockable; got result={result}"
