"""Labels_cleaned field persistence tests for fleet dispatch."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import autoskillit.fleet._api as fleet_api
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
            fleet_api,
            "parse_l3_result_block",
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
            fleet_api,
            "parse_l3_result_block",
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
            fleet_api,
            "parse_l3_result_block",
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
            fleet_api,
            "parse_l3_result_block",
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
            fleet_api,
            "parse_l3_result_block",
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
            fleet_api,
            "parse_l3_result_block",
            lambda **_: _make_completed_clean(True),
        )

        await _run(tool_ctx, ingredients={"issue_url": issue_url})

        record = _read_dispatch_record(tool_ctx)
        assert record["issue_url"] == issue_url


def _wire_killed_with_tracker_option(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    *,
    issue_url: str,
    write_tracker: bool,
):
    """Wire a process_killed dispatch with a fixed identity and a stateful GitHub fake.

    Shared between the RESUMABLE test and its no-tracker FAILURE control so the
    two scenarios differ only in whether the tracker file exists.
    """
    import dataclasses
    import json as _json

    from autoskillit.core import DispatchIdentity, InfraOutcome
    from autoskillit.fleet._checkpoint_bridge import load_dispatch_progress
    from autoskillit.fleet.campaign_state import state as campaign_state
    from autoskillit.recipe.schema import RecipeIngredient
    from tests.fakes import _DEFAULT_SKILL_RESULT, FakeGitHubFetcher

    _setup_dispatch(
        tool_ctx,
        monkeypatch,
        ingredients={"issue_url": RecipeIngredient(description="Issue URL")},
    )
    monkeypatch.setattr(fleet_api, "load_dispatch_progress", load_dispatch_progress)

    fixed_dispatch_id = "d1e2c3b4-a5f6-4789-8abc-def012345678"
    fixed_identity = DispatchIdentity.from_dispatch_id(fixed_dispatch_id)

    class _FixedDispatchIdentity:
        @classmethod
        def fresh(cls) -> DispatchIdentity:
            return fixed_identity

    monkeypatch.setattr(campaign_state, "DispatchIdentity", _FixedDispatchIdentity)

    owner, repo, number = "owner", "repo", 42
    github_client = FakeGitHubFetcher(issues={(owner, repo, number): {"labels": ["in-progress"]}})
    swap_spy = AsyncMock(wraps=github_client.swap_labels)
    github_client.swap_labels = swap_spy
    tool_ctx.github_client = github_client

    tool_ctx.executor = InMemoryHeadlessExecutor(
        default_result=dataclasses.replace(
            _DEFAULT_SKILL_RESULT,
            success=False,
            session_id="sess-resumable-001",
            lifespan_started=True,
            retry_reason="resume",
            infra=InfraOutcome(exit_category="process_killed"),
        )
    )
    monkeypatch.setattr(
        fleet_api,
        "parse_l3_result_block",
        lambda **_: _make_no_sentinel(),
    )

    if write_tracker:
        tracker_dir = tool_ctx.project_dir / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True, exist_ok=True)
        tracker_file = tracker_dir / f"{fixed_dispatch_id}.json"
        tracker_file.write_text(
            _json.dumps(
                {
                    "pipeline_id": fixed_dispatch_id,
                    "kitchen_id": "k-test",
                    "initialized_at": "2026-06-01T00:00:00Z",
                    "steps": {
                        "plan": {
                            "status": "complete",
                            "completed_at": "2026-06-01T00:01:00Z",
                        },
                        "implement": {
                            "status": "complete",
                            "completed_at": "2026-06-01T00:02:00Z",
                        },
                        "review-pr": {"status": "pending"},
                    },
                    "dependencies": {},
                }
            )
        )

    return github_client, swap_spy, (owner, repo, number)


class TestResumableLeavesLabelsUntouched:
    @pytest.mark.anyio
    async def test_resumable_status_leaves_labels_untouched(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """process_killed + tracker progress -> RESUMABLE; swap_labels is never awaited."""
        issue_url = "https://github.com/owner/repo/issues/42"
        github_client, swap_spy, key = _wire_killed_with_tracker_option(
            tool_ctx, monkeypatch, issue_url=issue_url, write_tracker=True
        )

        await _run(tool_ctx, ingredients={"issue_url": issue_url})

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "resumable"
        assert record["labels_cleaned"] is False
        swap_spy.assert_not_awaited()
        assert github_client.issues[key]["labels"] == {"in-progress"}

    @pytest.mark.anyio
    async def test_failure_without_tracker_swaps_labels(
        self, tool_ctx, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: identical wiring minus the tracker file classifies FAILURE and swaps labels.

        Proves the RESUMABLE assertions above discriminate on the guard rather than
        on the fixture wiring — dropping only the tracker file flips the outcome.
        """
        issue_url = "https://github.com/owner/repo/issues/42"
        github_client, swap_spy, key = _wire_killed_with_tracker_option(
            tool_ctx, monkeypatch, issue_url=issue_url, write_tracker=False
        )

        await _run(tool_ctx, ingredients={"issue_url": issue_url})

        record = _read_dispatch_record(tool_ctx)
        assert record["status"] == "failure"
        assert record["labels_cleaned"] is True
        swap_spy.assert_awaited_once()
        assert github_client.issues[key]["labels"] == {"fail"}
