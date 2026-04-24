"""Group G (franchise part): lifespan_started surface + envelope propagation."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository

pytestmark = [pytest.mark.layer("franchise"), pytest.mark.small, pytest.mark.feature("franchise")]


def _make_recipe_info(name: str = "test-recipe"):
    from pathlib import Path

    from autoskillit.recipe.schema import RecipeInfo, RecipeSource

    return RecipeInfo(
        name=name,
        description="test",
        source=RecipeSource.PROJECT,
        path=Path(f"/fake/{name}.yaml"),
    )


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


def _setup_dispatch(tool_ctx, monkeypatch, recipe_name: str = "test-recipe"):
    from autoskillit.recipe.schema import Recipe, RecipeKind

    tool_ctx.franchise_lock = asyncio.Lock()
    repo = InMemoryRecipeRepository()
    repo.add_recipe(recipe_name, _make_recipe_info(recipe_name))
    tool_ctx.recipes = repo
    tool_ctx.executor = InMemoryHeadlessExecutor()
    monkeypatch.setattr(
        "autoskillit.franchise._api.load_recipe",
        lambda _path: Recipe(
            name=recipe_name, description="test", kind=RecipeKind.STANDARD, ingredients={}
        ),
    )


async def _run(tool_ctx, recipe: str = "test-recipe") -> dict:
    from autoskillit.franchise._api import execute_dispatch

    raw = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe=recipe,
        task="t",
        ingredients=None,
        dispatch_name=None,
        timeout_sec=None,
        prompt_builder=_simple_prompt_builder,
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    return json.loads(raw)


def _make_completed_clean(success: bool):
    from autoskillit.franchise.result_parser import L2ParseResult

    return L2ParseResult(
        outcome="completed_clean",
        payload={"success": success},
        raw_body=None,
        parse_error=None,
        source="stdout",
    )


def _make_no_sentinel():
    from autoskillit.franchise.result_parser import L2ParseResult

    return L2ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )


def _make_completed_dirty():
    from autoskillit.franchise.result_parser import L2ParseResult

    return L2ParseResult(
        outcome="completed_dirty",
        payload=None,
        raw_body="bad",
        parse_error="json error",
        source="stdout",
    )


class TestLifespanStartedField:
    def test_skill_result_lifespan_started_field_exists(self):
        """SkillResult has a lifespan_started: bool field defaulting to False."""
        from autoskillit.core import SkillResult
        from autoskillit.core.types import RetryReason

        sr = SkillResult(
            success=True,
            result="ok",
            session_id="",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        assert hasattr(sr, "lifespan_started")
        assert sr.lifespan_started is False


class TestLifespanStartedInEnvelopes:
    @pytest.mark.anyio
    async def test_success_envelope_includes_lifespan_started(self, tool_ctx, monkeypatch):
        """completed_clean success envelope includes 'lifespan_started' key."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                lifespan_started=True,
            )
        )
        monkeypatch.setattr(
            "autoskillit.franchise._api.parse_l2_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        result = await _run(tool_ctx)
        assert result["success"] is True
        assert "lifespan_started" in result
        assert result["lifespan_started"] is True

    @pytest.mark.anyio
    async def test_failure_envelope_includes_lifespan_started_no_sentinel(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel envelope includes 'lifespan_started' field."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.franchise._api.parse_l2_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert result["success"] is False
        assert "lifespan_started" in result
        assert result["lifespan_started"] is False

    @pytest.mark.anyio
    async def test_failure_envelope_includes_lifespan_started_completed_dirty(
        self, tool_ctx, monkeypatch
    ):
        """completed_dirty envelope includes 'lifespan_started' field."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.franchise._api.parse_l2_result_block",
            lambda **_: _make_completed_dirty(),
        )

        result = await _run(tool_ctx)
        assert result["success"] is False
        assert "lifespan_started" in result
