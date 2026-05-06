"""Group F: Timeout + No-Result-Block failure semantics for fleet dispatch."""

from __future__ import annotations

import json

import pytest

from autoskillit.fleet.result_parser import L3ParseResult
from tests.fakes import InMemoryHeadlessExecutor
from tests.fleet._helpers import _setup_dispatch

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _simple_prompt_builder(**kwargs) -> str:
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


async def _no_sleep_quota_checker(config, **kwargs) -> dict:
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config, **kwargs) -> None:
    pass


async def _run(
    tool_ctx, recipe: str = "test-recipe", ingredients: dict[str, str] | None = None
) -> dict:
    from autoskillit.fleet._api import execute_dispatch

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe=recipe,
        task="t",
        ingredients=ingredients,
        dispatch_name=None,
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    return json.loads(raw)


def _read_dispatch_record(tool_ctx) -> dict:
    """Read the single dispatch record written to the state file."""
    state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
    assert len(state_files) == 1, f"Expected 1 state file, found {len(state_files)}"
    state = json.loads(state_files[0].read_text())
    return state["dispatches"][0]


def _make_no_sentinel():

    return L3ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )


def _make_completed_dirty():

    return L3ParseResult(
        outcome="completed_dirty",
        payload=None,
        raw_body="garbled",
        parse_error="json decode error",
        source="stdout",
    )


def _make_completed_clean(success: bool, reason: str = ""):

    payload: dict = {"success": success}
    if reason:
        payload["reason"] = reason
    return L3ParseResult(
        outcome="completed_clean",
        payload=payload,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )


class TestTimeoutPath:
    @pytest.mark.anyio
    async def test_timeout_returns_fleet_error_envelope(self, tool_ctx, monkeypatch):
        """skill_result.subtype == 'timeout' → fleet_error envelope with error='l3_timeout'."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, subtype="timeout")
        )

        result = await _run(tool_ctx)
        assert result["success"] is False
        assert result["error"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timeout_writes_state_with_reason_l3_timeout(self, tool_ctx, monkeypatch):
        """Timeout path writes DispatchRecord with status=failure and reason=l3_timeout."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, subtype="timeout")
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "failure"
        assert record["reason"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timeout_skips_parse_l3_result_block(self, tool_ctx, monkeypatch):
        """Timeout path must not call parse_l3_result_block."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, subtype="timeout")
        )

        def _should_not_be_called(**_kwargs):
            raise AssertionError("parse_l3_result_block called on timeout path")

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            _should_not_be_called,
        )

        # Should succeed (return l3_timeout error envelope) without raising
        result = await _run(tool_ctx)
        assert result["error"] == "fleet_l3_timeout"

    @pytest.mark.anyio
    async def test_timeout_envelope_includes_dispatch_metadata(self, tool_ctx, monkeypatch):
        """Timeout envelope includes dispatch_id, dispatched_session_id, and token_usage."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                subtype="timeout",
                session_id="sess-timeout-123",
                token_usage={"input_tokens": 50},
            )
        )

        result = await _run(tool_ctx)
        details = result.get("details", {})
        assert "dispatch_id" in details
        assert details["dispatched_session_id"] == "sess-timeout-123"
        assert details["token_usage"] == {"input_tokens": 50}

    @pytest.mark.anyio
    async def test_idle_stall_falls_through_to_parse(self, tool_ctx, monkeypatch):
        """idle_stall subtype must NOT trigger the timeout pre-check; parse is called."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                subtype="idle_stall",
                success=False,
            )
        )

        parse_called = []

        def _recording_parse(**kwargs):
            parse_called.append(True)
            return _make_no_sentinel()

        monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _recording_parse)

        await _run(tool_ctx)
        assert parse_called, "parse_l3_result_block was not called for idle_stall"


class TestNoSentinelPath:
    @pytest.mark.anyio
    async def test_no_sentinel_writes_state_with_reason_l3_no_result_block(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel outcome → DispatchRecord.reason = 'l3_no_result_block'."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == "fleet_l3_no_result_block"

    @pytest.mark.anyio
    async def test_no_sentinel_clean_exit_is_not_success(self, tool_ctx, monkeypatch):
        """no_sentinel outcome → envelope.success=False even when SkillResult.success=True."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=True,
                exit_code=0,
            )
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert result["success"] is False


class TestCompletedDirtyPath:
    @pytest.mark.anyio
    async def test_completed_dirty_writes_state_with_reason_l3_parse_failed(
        self, tool_ctx, monkeypatch
    ):
        """completed_dirty outcome → DispatchRecord.reason = 'l3_parse_failed'."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_dirty(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == "fleet_l3_parse_failed"


class TestDispatchStatusEnvelopeField:
    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_success(self, tool_ctx, monkeypatch):
        """Envelope from _run_dispatch includes dispatch_status matching state-file status."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "success"

    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_failure(self, tool_ctx, monkeypatch):
        """Envelope includes dispatch_status='failure' when outcome is completed_dirty."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_dirty(),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "failure"

    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_no_sentinel(self, tool_ctx, monkeypatch):
        """Envelope includes dispatch_status='failure' for no_sentinel without session signal."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "failure"

    @pytest.mark.anyio
    async def test_envelope_includes_dispatch_status_on_no_sentinel_resumable(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel + session_id + lifespan_started + sidecar → dispatch_status='resumable'."""
        import dataclasses
        from uuid import UUID

        from tests.fakes import _DEFAULT_SKILL_RESULT, InMemoryHeadlessExecutor

        _setup_dispatch(tool_ctx, monkeypatch)

        fixed_dispatch_id = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        monkeypatch.setattr("autoskillit.fleet._api.uuid4", lambda: UUID(fixed_dispatch_id))

        from autoskillit.fleet.sidecar import sidecar_path

        sidecar_file = sidecar_path(fixed_dispatch_id, tool_ctx.project_dir)
        sidecar_file.parent.mkdir(parents=True, exist_ok=True)
        sidecar_file.touch()

        resumable_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            session_id="sess-resumable-abc",
            lifespan_started=True,
        )

        class _SpawningExecutor(InMemoryHeadlessExecutor):
            """Calls on_spawn with a fake PID to drive PENDING → RUNNING before returning."""

            async def dispatch_food_truck(self, orchestrator_prompt, cwd, *, on_spawn=None, **kw):
                if on_spawn is not None:
                    on_spawn(12345, 1000)
                return await super().dispatch_food_truck(
                    orchestrator_prompt, cwd, on_spawn=on_spawn, **kw
                )

        tool_ctx.executor = _SpawningExecutor(default_result=resumable_result)

        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert "dispatch_status" in result
        assert result["dispatch_status"] == "resumable"


class TestCompletedCleanPath:
    @pytest.mark.anyio
    async def test_completed_clean_success_writes_empty_reason(self, tool_ctx, monkeypatch):
        """completed_clean with success=True → DispatchRecord.reason = ''."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == ""

    @pytest.mark.anyio
    async def test_completed_clean_failure_writes_reason_from_payload(self, tool_ctx, monkeypatch):
        """completed_clean success=False: payload.reason → DispatchRecord.reason."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=False, reason="my-failure-reason"),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["reason"] == "my-failure-reason"


# ---------------------------------------------------------------------------
# Group: Missing required ingredient validation
# ---------------------------------------------------------------------------


def _setup_dispatch_with_ingredients(tool_ctx, monkeypatch, ingredients: dict):
    """Wire tool_ctx with a recipe that has specific ingredients."""
    from autoskillit.fleet import FleetSemaphore
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
    from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

    tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
    repo = InMemoryRecipeRepository()
    recipe_info = _make_recipe_info("test-recipe")
    repo.add_recipe("test-recipe", recipe_info)
    repo.add_full_recipe(
        recipe_info.path,
        Recipe(
            name="test-recipe",
            description="test",
            kind=RecipeKind.STANDARD,
            ingredients={
                k: RecipeIngredient(description=f"desc-{k}", **v) for k, v in ingredients.items()
            },
        ),
    )
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()


def _make_recipe_info(name: str = "test-recipe"):
    from tests.fleet._helpers import _make_recipe_info as _base

    return _base(name)


class TestMissingRequiredIngredient:
    @pytest.mark.anyio
    async def test_dispatch_rejects_missing_required_ingredient(self, tool_ctx, monkeypatch):
        """Required ingredient with no default → FLEET_MISSING_INGREDIENT."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"api_key": {"required": True, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result["success"] is False
        assert result["error"] == "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_allows_required_ingredient_when_supplied(self, tool_ctx, monkeypatch):
        """A required ingredient that IS supplied passes validation."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"api_key": {"required": True, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={"api_key": "secret"})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_allows_required_ingredient_with_default(self, tool_ctx, monkeypatch):
        """A required ingredient with a non-None default passes even when not supplied."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"api_key": {"required": True, "default": "fallback"}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_dispatch_lists_all_missing_required_ingredients(self, tool_ctx, monkeypatch):
        """When multiple required ingredients are missing, all are listed."""
        _setup_dispatch_with_ingredients(
            tool_ctx,
            monkeypatch,
            {
                "key_a": {"required": True, "default": None},
                "key_b": {"required": True, "default": None},
            },
        )

        result = await _run(tool_ctx, ingredients={})
        assert result["success"] is False
        assert result["error"] == "fleet_missing_ingredient"
        assert "key_a" in result["user_visible_message"]
        assert "key_b" in result["user_visible_message"]

    @pytest.mark.anyio
    async def test_dispatch_ignores_optional_missing_ingredients(self, tool_ctx, monkeypatch):
        """Optional ingredients (required=False) don't trigger missing-ingredient errors."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"optional_key": {"required": False, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_task_auto_injected_from_top_level_param(self, tool_ctx, monkeypatch):
        """top-level task param auto-injects into effective_ingredients when recipe declares it."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"task": {"required": True, "default": None}}
        )

        result = await _run(tool_ctx, ingredients={})
        assert result.get("error") != "fleet_missing_ingredient"

    @pytest.mark.anyio
    async def test_explicit_ingredient_task_overrides_top_level(self, tool_ctx, monkeypatch):
        """Explicit ingredients['task'] takes precedence over top-level task param."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"task": {"required": True, "default": None}}
        )

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="top-level-value",
            ingredients={"task": "override-value"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_capture_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        assert captured["ingredients"]["task"] == "override-value"

    @pytest.mark.anyio
    async def test_task_not_injected_when_not_declared_ingredient(self, tool_ctx, monkeypatch):
        """Top-level task is NOT injected when recipe has no 'task' ingredient key."""
        _setup_dispatch_with_ingredients(
            tool_ctx, monkeypatch, {"other_key": {"required": False, "default": "x"}}
        )

        captured = {}

        def _capture_prompt_builder(**kwargs):
            captured.update(kwargs)
            return "prompt"

        from autoskillit.fleet._api import execute_dispatch

        await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="some-task",
            ingredients={},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=_capture_prompt_builder,
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        assert "task" not in captured["ingredients"]


# ---------------------------------------------------------------------------
# Group: Recipe kind dispatch gate
# ---------------------------------------------------------------------------


class TestRecipeKindDispatchGate:
    """Verify that dispatch gate accepts/rejects by RecipeKind."""

    def _setup_food_truck_recipe(self, tool_ctx):
        """Wire tool_ctx with a food-truck kind recipe."""
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.FOOD_TRUCK,
                ingredients={},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    def _setup_campaign_recipe(self, tool_ctx):
        """Wire tool_ctx with a campaign kind recipe."""
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.recipe.schema import Recipe, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info("test-recipe")
        repo.add_recipe("test-recipe", recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name="test-recipe",
                description="test",
                kind=RecipeKind.CAMPAIGN,
                ingredients={},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

    @pytest.mark.anyio
    async def test_food_truck_dispatchable(self, tool_ctx, monkeypatch):
        """T4: FOOD_TRUCK kind is accepted by the dispatch gate (not rejected)."""
        self._setup_food_truck_recipe(tool_ctx)

        result = await _run(tool_ctx)
        # Any error OTHER than fleet_invalid_recipe_kind means the gate passed
        assert result.get("error") != "fleet_invalid_recipe_kind"

    @pytest.mark.anyio
    async def test_campaign_kind_still_rejected_by_dispatch(self, tool_ctx, monkeypatch):
        """T5: CAMPAIGN kind is still rejected by the dispatch gate."""
        self._setup_campaign_recipe(tool_ctx)

        result = await _run(tool_ctx)
        assert result["success"] is False
        assert result["error"] == "fleet_invalid_recipe_kind"
