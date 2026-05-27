"""Tests for DispatchStateHandle and dispatch state invariants (Group J)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _dispatches_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dispatches"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestResumeWithoutPriorDispatchId:
    @pytest.mark.anyio
    async def test_resume_without_prior_dispatch_id_creates_state_file(
        self, tool_ctx, monkeypatch
    ):
        from tests.fleet._helpers import (
            _no_sleep_quota_checker,
            _noop_quota_refresher,
            _setup_dispatch,
        )

        _setup_dispatch(tool_ctx, monkeypatch)
        from autoskillit.fleet._api import _run_dispatch

        await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="do something",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="sess-123",
            prior_dispatch_id=None,
        )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        state_files = list(dispatches_dir.glob("*.json"))
        assert len(state_files) == 1
        assert state_files[0].exists()

        from autoskillit.fleet.state import read_state

        state = read_state(state_files[0])
        assert state is not None
        assert len(state.dispatches) == 1

    @pytest.mark.anyio
    async def test_resume_without_prior_dispatch_id_persists_captures(
        self, tool_ctx, monkeypatch, tmp_path
    ):
        from autoskillit.core.types import CaptureEntrySpec
        from autoskillit.fleet.result_parser import L3ParseResult
        from tests.fleet._helpers import (
            _no_sleep_quota_checker,
            _noop_quota_refresher,
            _setup_dispatch,
        )

        _setup_dispatch(tool_ctx, monkeypatch)
        jsonl_file = tmp_path / "sess-123.jsonl"
        jsonl_file.touch()
        monkeypatch.setattr("autoskillit.fleet._api.claude_code_log_path", lambda *_: jsonl_file)

        mock_parsed = L3ParseResult(
            outcome="completed_clean",
            payload={"success": True, "plan_path": "/tmp/my-plan.md"},
            source="stdout",
            raw_body="",
            parse_error=None,
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: mock_parsed,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.classify_dispatch_outcome",
            lambda *a, **kw: (DispatchStatus.SUCCESS, None),
        )

        from autoskillit.fleet._api import _run_dispatch
        from autoskillit.fleet.state import read_state

        await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="do something",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="sess-123",
            prior_dispatch_id=None,
            capture={
                "plan_path": CaptureEntrySpec(from_="${{ result.plan_path }}", value_type="string")
            },
        )

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        state_files = list(dispatches_dir.glob("*.json"))
        assert len(state_files) == 1
        state = read_state(state_files[0])
        assert state is not None
        assert state.captured_values == {"plan_path": "/tmp/my-plan.md"}


class TestDispatchStateHandleFactory:
    def test_dispatch_state_handle_create_fresh_writes_file(self, tmp_path):
        from autoskillit.fleet.state import DispatchStateHandle, read_state

        d = _dispatches_dir(tmp_path)
        handle = DispatchStateHandle.create_fresh(
            d, "camp-1", "my-campaign", "", [DispatchRecord(name="d1")], None
        )
        assert handle.state_path.exists()
        assert handle.identity.dispatch_id in handle.state_path.name
        state = read_state(handle.state_path)
        assert state is not None
        assert state.campaign_name == "my-campaign"
        assert len(state.dispatches) == 1
        assert state.dispatches[0].name == "d1"

    def test_dispatch_state_handle_open_continued_rejects_missing_file(self, tmp_path):
        from autoskillit.fleet.state import DispatchStateHandle

        d = _dispatches_dir(tmp_path)
        with pytest.raises(FileNotFoundError):
            DispatchStateHandle.open_continued(d, "nonexistent-id-abc-123")

    def test_dispatch_state_handle_open_continued_succeeds_with_existing_file(self, tmp_path):
        from autoskillit.fleet.state import DispatchStateHandle

        d = _dispatches_dir(tmp_path)
        known_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        state_path = d / f"{known_id}.json"
        write_initial_state(state_path, "camp-1", "my-campaign", "", [DispatchRecord(name="d1")])

        handle = DispatchStateHandle.open_continued(d, known_id)
        assert handle.state_path.exists()
        assert handle.identity.dispatch_id == known_id


class TestAllResumeCombinationsProduceValidHandle:
    @pytest.mark.parametrize(
        "resume,prior,expect_fresh",
        [
            (None, None, True),
            (None, "abc-def-ghi-jkl-mno", True),
            ("sess-1", "abc-def-ghi-jkl-mno", False),
            ("sess-1", None, True),
            ("sess-1", "", True),
        ],
    )
    def test_all_resume_combinations_produce_valid_handle(
        self, tmp_path, resume, prior, expect_fresh
    ):
        from autoskillit.fleet.state import DispatchStateHandle

        d = _dispatches_dir(tmp_path)

        if not expect_fresh and prior:
            state_path = d / f"{prior}.json"
            write_initial_state(
                state_path, "camp-1", "my-campaign", "", [DispatchRecord(name="d1")]
            )

        if resume and prior:
            handle = DispatchStateHandle.open_continued(d, prior)
        else:
            handle = DispatchStateHandle.create_fresh(
                d, "camp-1", "my-campaign", "", [DispatchRecord(name="d1")], None
            )

        assert handle.state_path.exists()
        assert handle.identity.dispatch_id in handle.state_path.name
        if not expect_fresh and prior:
            assert handle.identity.dispatch_id == prior


class TestCaptureChainAcrossResumeBoundary:
    @pytest.mark.anyio
    async def test_capture_chain_across_resume_boundary(self, tool_ctx, monkeypatch):
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import (
            FleetSemaphore,
            upsert_dispatch_record_by_name,
            write_captured_values,
        )
        from autoskillit.fleet._api import _run_dispatch
        from autoskillit.fleet.state_types import DispatchRejected
        from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
        from tests.fleet._helpers import (
            _make_recipe_info,
            _no_sleep_quota_checker,
            _noop_quota_refresher,
        )

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)

        recipe_name = "capture-recipe"
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info(recipe_name)
        repo.add_recipe(recipe_name, recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name=recipe_name,
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={"plan_path": RecipeIngredient(description="plan", required=True)},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)
        campaign_id = tool_ctx.kitchen_id

        prior_id = "prev-dispatch-abc123"
        prev_state_path = dispatches_dir / f"{prior_id}.json"
        write_initial_state(
            prev_state_path, campaign_id, "camp", "", [DispatchRecord(name="dispatch-a")]
        )
        upsert_dispatch_record_by_name(
            prev_state_path, DispatchRecord(name="dispatch-a", status=DispatchStatus.SUCCESS)
        )
        write_captured_values(prev_state_path, {"plan_path": "/tmp/p"})

        result = await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe=recipe_name,
            task="do something",
            ingredients={"plan_path": "${{ campaign.plan_path }}"},
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="sess-resume-1",
            prior_dispatch_id=prior_id,
        )

        result = result.outcome
        if isinstance(result, DispatchRejected):
            assert result.error_code != FleetErrorCode.FLEET_UNKNOWN_INGREDIENT, (
                f"Campaign capture ref not resolved — captures lost: {result.message}"
            )
        else:
            from autoskillit.fleet.state_types import DispatchCompleted

            assert isinstance(result, DispatchCompleted), f"Unexpected result type: {type(result)}"
            assert result.dispatch_id == prior_id


class TestSessionChainAccumulatesAcrossResume:
    @pytest.mark.anyio
    async def test_session_chain_accumulates_across_resume(self, tool_ctx, monkeypatch, tmp_path):
        """session_chain accumulates prior dispatched_session_id on resume."""
        from autoskillit.core import RetryReason, SkillResult
        from autoskillit.fleet._api import _run_dispatch
        from autoskillit.fleet.state import read_state
        from tests.fleet._helpers import (
            _no_sleep_quota_checker,
            _noop_quota_refresher,
            _setup_dispatch,
        )

        _setup_dispatch(tool_ctx, monkeypatch)
        jsonl_file = tmp_path / "sess-resume-1.jsonl"
        jsonl_file.touch()
        monkeypatch.setattr("autoskillit.fleet._api.claude_code_log_path", lambda *_: jsonl_file)

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        campaign_id = tool_ctx.kitchen_id

        prior_id = "prior-dispatch-abc123"
        prior_state_path = dispatches_dir / f"{prior_id}.json"
        write_initial_state(
            prior_state_path,
            campaign_id,
            "camp",
            "",
            [DispatchRecord(name="test-recipe", dispatched_session_id="sess-original")],
        )

        tool_ctx.executor.push(
            SkillResult(
                success=True,
                result="ok",
                session_id="sess-resume-1",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
                token_usage=None,
            )
        )

        from autoskillit.fleet.result_parser import L3ParseResult

        def _fake_parse(*args, **kwargs):
            return L3ParseResult(
                outcome="completed_clean",
                payload={"success": True},
                raw_body=None,
                parse_error=None,
                source="stdout",
            )

        def _fake_classify(*args, **kwargs):
            return (DispatchStatus.SUCCESS, None)

        monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _fake_parse)
        monkeypatch.setattr("autoskillit.fleet.classify_dispatch_outcome", _fake_classify)

        result = await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="do something",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
            resume_session_id="sess-resume-1",
            prior_dispatch_id=prior_id,
        )

        from autoskillit.fleet.state_types import DispatchRejected

        assert not isinstance(result, DispatchRejected), f"Unexpected rejection: {result}"

        state = read_state(prior_state_path)
        assert state is not None
        disp = next(d for d in state.dispatches if d.dispatch_id == prior_id)
        assert "sess-original" in disp.session_chain
        assert disp.dispatched_session_id == "sess-resume-1"


class TestDispatchedSessionLogDirPopulated:
    @pytest.mark.anyio
    async def test_dispatched_session_log_dir_populated(self, tool_ctx, monkeypatch):
        """dispatched_session_log_dir is populated after _run_dispatch completes."""
        from autoskillit.fleet import FleetSemaphore
        from autoskillit.fleet._api import _run_dispatch
        from autoskillit.fleet.state import read_state
        from autoskillit.recipe.schema import Recipe, RecipeKind
        from tests.fakes import InMemoryHeadlessExecutor, InMemoryRecipeRepository
        from tests.fleet._helpers import (
            _make_recipe_info,
            _no_sleep_quota_checker,
            _noop_quota_refresher,
        )

        tool_ctx.fleet_lock = FleetSemaphore(max_concurrent=1)
        recipe_name = "test-recipe-logdir"
        repo = InMemoryRecipeRepository()
        recipe_info = _make_recipe_info(recipe_name)
        repo.add_recipe(recipe_name, recipe_info)
        repo.add_full_recipe(
            recipe_info.path,
            Recipe(
                name=recipe_name,
                description="test",
                kind=RecipeKind.STANDARD,
                ingredients={},
                requires_packs=[],
            ),
        )
        tool_ctx.recipes = repo
        tool_ctx.executor = InMemoryHeadlessExecutor()

        dispatches_dir = tool_ctx.temp_dir / "dispatches"
        dispatches_dir.mkdir(parents=True, exist_ok=True)

        from autoskillit.fleet.result_parser import L3ParseResult

        def _fake_parse(*args, **kwargs):
            return L3ParseResult(
                outcome="completed_clean",
                payload={"success": True},
                raw_body=None,
                parse_error=None,
                source="stdout",
            )

        def _fake_classify(*args, **kwargs):
            return (DispatchStatus.SUCCESS, None)

        monkeypatch.setattr("autoskillit.fleet._api.parse_l3_result_block", _fake_parse)
        monkeypatch.setattr("autoskillit.fleet.classify_dispatch_outcome", _fake_classify)

        result = await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe=recipe_name,
            task="do something",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **_: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )

        from autoskillit.fleet.state_types import DispatchRejected

        assert not isinstance(result, DispatchRejected), f"Unexpected rejection: {result}"

        state_files = list(dispatches_dir.glob("*.json"))
        assert len(state_files) == 1
        state = read_state(state_files[0])
        assert state is not None
        assert len(state.dispatches) == 1
        disp = state.dispatches[0]
        assert disp.dispatched_session_log_dir != ""
