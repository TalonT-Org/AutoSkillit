"""Labels_cleaned field persistence tests for fleet dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from tests.fakes import InMemoryHeadlessExecutor
from tests.fleet._helpers import (
    _make_completed_clean,
    _make_no_sentinel,
    _read_dispatch_record,
    _run,
    _setup_dispatch,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestLabelsCleanedFieldPersistence:
    @pytest.mark.anyio
    async def test_labels_cleaned_true_when_cleanup_succeeds(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DispatchRecord persists labels_cleaned=True only when cleanup actually succeeds."""
        import dataclasses

        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path
        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        failure_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            success=False,
            result='{"success": false, "reason": "context_exhaustion"}',
            subtype="success",
            is_error=False,
            exit_code=0,
        )

        async def _return_failure(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/1",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            if kwargs.get("on_spawn"):
                kwargs["on_spawn"](12345, 1000)
            return failure_result

        tool_ctx.executor.dispatch_food_truck = _return_failure
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "failure"
        assert record["labels_cleaned"] is True

    @pytest.mark.anyio
    async def test_labels_cleaned_false_when_cleanup_fails(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DispatchRecord persists labels_cleaned=False when cleanup fails."""
        import dataclasses

        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path
        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        swap_labels_mock = AsyncMock(side_effect=Exception("rate limited"))
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        failure_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            success=False,
            result='{"success": false, "reason": "context_exhaustion"}',
            subtype="success",
            is_error=False,
            exit_code=0,
        )

        async def _return_failure(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/1",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            if kwargs.get("on_spawn"):
                kwargs["on_spawn"](12345, 1000)
            return failure_result

        tool_ctx.executor.dispatch_food_truck = _return_failure
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "failure"
        assert record["labels_cleaned"] is False

    @pytest.mark.anyio
    async def test_labels_cleaned_false_on_success_outcome(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DispatchRecord persists labels_cleaned=False when outcome is SUCCESS."""
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.github_client = None
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(True),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "success"
        assert record["labels_cleaned"] is False

    @pytest.mark.anyio
    async def test_labels_cleaned_true_with_no_client_no_sidecar(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """labels_cleaned=True when github_client is None (vacuously true — nothing to clean)."""
        import dataclasses

        from tests.fakes import _DEFAULT_SKILL_RESULT

        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.github_client = None

        failure_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            success=False,
            result='{"success": false, "reason": "context_exhaustion"}',
            subtype="success",
            is_error=False,
            exit_code=0,
        )
        tool_ctx.executor = InMemoryHeadlessExecutor(default_result=failure_result)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await _run(tool_ctx)

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "failure"
        assert record["labels_cleaned"] is True

    @pytest.mark.anyio
    async def test_singular_key_ingredient_triggers_fallback_cleanup(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Singular ``issue_url`` ingredient triggers cleanup via the ``issue_url`` fallback.

        Without sidecar entries, ``cleanup_orphaned_labels`` falls back to the
        ``issue_url`` kwarg. This test exercises that path with the singular
        ``issue_url`` recipe ingredient (used by implementation/remediation).
        Regression guard for issue #4112.
        """
        import dataclasses

        from autoskillit.recipe.schema import RecipeIngredient
        from tests.fakes import _DEFAULT_SKILL_RESULT

        issue_url = "https://github.com/owner/repo/issues/42"
        _setup_dispatch(
            tool_ctx,
            monkeypatch,
            ingredients={"issue_url": RecipeIngredient(description="Issue URL")},
        )
        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        failure_result = dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            success=False,
            result='{"success": false, "reason": "context_exhaustion"}',
            subtype="success",
            is_error=False,
            exit_code=0,
        )
        # No sidecar is written — exercises the issue_url fallback path.
        tool_ctx.executor = InMemoryHeadlessExecutor(default_result=failure_result)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        await _run(tool_ctx, ingredients={"issue_url": issue_url})

        # swap_labels should have been called via the issue_url fallback path.
        swap_labels_mock.assert_awaited_once()
        call_list = swap_labels_mock.call_args_list
        assert len(call_list) == 1
        owner_arg, repo_arg, number_arg = call_list[0].args
        assert owner_arg == "owner"
        assert repo_arg == "repo"
        assert number_arg == 42

    @pytest.mark.anyio
    async def test_singular_key_ingredient_populates_dispatch_record_issue_url(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DispatchRecord.issue_url is populated from the singular ``issue_url`` ingredient.

        Regression guard for issue #4112: the field was previously stored as
        ``""`` for single-issue recipes because ``_api.py`` only read the
        plural ``issue_urls`` key.
        """
        import dataclasses

        from autoskillit.recipe.schema import RecipeIngredient
        from tests.fakes import _DEFAULT_SKILL_RESULT

        issue_url = "https://github.com/owner/repo/issues/42"
        _setup_dispatch(
            tool_ctx,
            monkeypatch,
            ingredients={"issue_url": RecipeIngredient(description="Issue URL")},
        )
        tool_ctx.github_client = None  # skip label cleanup
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT,
                success=True,
                result='{"success": true}',
                subtype="success",
                is_error=False,
                exit_code=0,
            )
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(True),
        )

        await _run(tool_ctx, ingredients={"issue_url": issue_url})

        record = _read_dispatch_record(tool_ctx)
        assert record["issue_url"] == issue_url
