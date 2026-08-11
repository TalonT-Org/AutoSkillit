"""Tests for record_pipeline_step MCP tool."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import (
    complete_run_skill_result,
    record_pipeline_step,
)
from tests.server._helpers import _with_finalized_projection
from tests.server._pipeline_test_helpers import _grant_success_credit
from tests.server.conftest import _set_mock_kitchen_transition

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRecordPipelineStepInit:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        tool_ctx_kitchen_open.active_recipe_steps = {
            "review": {},
            "implement": {},
            "verify": {},
        }
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_init_creates_tracker_file(self):
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="AB",
                op="init",
                dependencies={"implement": ["review"]},
            )
        )
        assert result["success"] is True
        assert result["step_count"] == 3
        assert result["dependency_count"] == 1

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        assert tracker["pipeline_id"] == "AB"
        assert tracker["kitchen_id"] == "test-kitchen"
        assert set(tracker["steps"].keys()) == {"review", "implement", "verify"}
        assert tracker["dependencies"] == {"implement": ["review"]}

    @pytest.mark.anyio
    async def test_init_marks_locked_steps_as_skipped(self):
        overlay_path = self.tmp_path / ".autoskillit" / "temp" / ".hook_config_overlay.json"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text(json.dumps({"locked_steps": {"AB": {"verify": False}}}))

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is True

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        tracker = json.loads(tracker_path.read_text())
        assert tracker["steps"]["verify"]["status"] == "skipped"
        assert tracker["steps"]["review"]["status"] == "pending"

    @pytest.mark.anyio
    async def test_init_rejects_corrupt_overlay_state(self):
        overlay_path = self.tmp_path / ".autoskillit" / "temp" / ".hook_config_overlay.json"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text("{ malformed")

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))

        assert result["success"] is False
        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        assert not tracker_path.exists()

    @pytest.mark.anyio
    async def test_init_pipeline_id_fallback_to_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "XY")
        result = json.loads(await record_pipeline_step(pipeline_id="", op="init"))
        assert result["success"] is True
        assert result["pipeline_id"] == "XY"

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "XY.json"
        assert tracker_path.exists()

    @pytest.mark.anyio
    async def test_init_rejects_duplicate_pipeline_id(self):
        await record_pipeline_step(pipeline_id="AB", op="init")
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is False
        assert "already been initialized" in result["error"]

    @pytest.mark.anyio
    async def test_init_empty_pipeline_id_no_env_returns_error(self, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        result = json.loads(await record_pipeline_step(pipeline_id="", op="init"))
        assert result["success"] is False
        assert "pipeline_id is required" in result["error"]

    @pytest.mark.anyio
    async def test_terminal_completion_releases_manual_tracker_lease(self):
        self.ctx.active_recipe_steps = {"review": {}}
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        assert [key.owner_kind for key in self.ctx.tracker_leases] == ["manual"]
        _grant_success_credit(self.ctx, self.tmp_path, "review", pipeline_id="AB")

        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is True
        assert completed["done"] == completed["total"] == 1
        assert self.ctx.tracker_leases == {}

    @pytest.mark.anyio
    async def test_close_kitchen_releases_partial_pipeline_lease(self):
        from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

        self.ctx.active_recipe_steps = {"review": {}, "implement": {}}
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        key, lease = next(iter(self.ctx.tracker_leases.items()))
        tracker_path = key.target.path
        _grant_success_credit(self.ctx, self.tmp_path, "review", pipeline_id="AB")

        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is True
        assert completed["done"] == 1
        assert completed["total"] == 2
        assert self.ctx.tracker_leases == {key: lease}
        assert not lease.closed

        _close_kitchen_handler()

        assert self.ctx.tracker_leases == {}
        assert lease.closed
        assert not tracker_path.exists()

    @pytest.mark.anyio
    async def test_kitchen_release_preserves_manual_tracker_lease(self):
        from autoskillit.server.tools.tools_kitchen import (
            _release_kitchen_tracker_authority,
            _retain_kitchen_tracker_authority,
        )
        from autoskillit.server.tools.tools_pipeline_tracker import _release_context_tracker

        _retain_kitchen_tracker_authority(self.ctx)
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        manual_key = next(key for key in self.ctx.tracker_leases if key.owner_kind == "manual")

        _release_kitchen_tracker_authority(self.ctx, unregister=False, retire=False)

        assert list(self.ctx.tracker_leases) == [manual_key]
        assert not self.ctx.tracker_leases[manual_key].closed
        _release_context_tracker(self.ctx, manual_key)

    def test_kitchen_release_does_external_work_outside_lease_lock(self, monkeypatch):
        from autoskillit.server.tools import tools_kitchen

        tools_kitchen._retain_kitchen_tracker_authority(self.ctx)
        lock_states = []

        def record_lock_state(_value):
            lock_states.append(getattr(self.ctx.tracker_leases_lock, "_is_owned")())

        monkeypatch.setattr(tools_kitchen, "unregister_active_kitchen", record_lock_state)
        monkeypatch.setattr(tools_kitchen, "try_retire_tracker", record_lock_state)

        tools_kitchen._release_kitchen_tracker_authority(
            self.ctx,
            unregister=True,
            retire=True,
        )

        assert lock_states == [False, False]

    @pytest.mark.anyio
    async def test_completion_exception_releases_manual_tracker_lease(self, monkeypatch):
        from autoskillit.server.tools import tools_pipeline_tracker

        self.ctx.active_recipe_steps = {"review": {}}
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        _grant_success_credit(self.ctx, self.tmp_path, "review", pipeline_id="AB")

        def raise_from_marker(*_args, **_kwargs):
            raise OSError("marker failed")

        monkeypatch.setattr(tools_pipeline_tracker, "mark_step_complete", raise_from_marker)
        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is False
        assert completed["is_error"] is True
        assert completed["stage"] == "pipeline_marker"
        assert completed["error"] == "record_pipeline_step: pipeline marker failed."
        assert self.ctx.tracker_leases == {}

    @pytest.mark.anyio
    async def test_completion_identity_read_exception_releases_manual_lease(self, monkeypatch):
        from autoskillit.server.tools import tools_pipeline_tracker

        def fail_read(*_args, **_kwargs):
            raise OSError("identity read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_authority", fail_read)
        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is False
        assert self.ctx.tracker_leases == {}

    @pytest.mark.anyio
    async def test_manual_init_preserves_existing_corrupt_bytes_and_releases_lease(self):
        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        tracker_path.parent.mkdir(parents=True)
        tracker_path.write_bytes(b"{not-json")

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))

        assert result["success"] is False
        assert tracker_path.read_bytes() == b"{not-json"
        assert self.ctx.tracker_leases == {}

    @pytest.mark.parametrize(
        ("op", "handler"), [("init", "_handle_init"), ("status", "_handle_status")]
    )
    @pytest.mark.anyio
    async def test_handler_exception_releases_new_manual_lease(self, monkeypatch, op, handler):
        from autoskillit.server.tools import tools_pipeline_tracker

        def fail(*_args, **_kwargs):
            raise OSError("handler failed")

        monkeypatch.setattr(tools_pipeline_tracker, handler, fail)
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op=op))

        assert result["success"] is False
        assert self.ctx.tracker_leases == {}


class TestRecordPipelineStepGateClosed:
    @pytest.mark.anyio
    async def test_init_rejects_without_kitchen_open(self, tool_ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx.project_dir = tmp_path
        tool_ctx.active_recipe_steps = {"step_a": {}}
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is False


class TestCompleteRunSkillResult:
    @pytest.mark.anyio
    async def test_tracker_preparation_failure_keeps_acknowledged_credit_repairable(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace

        from autoskillit.server.tools import tools_pipeline_tracker

        authority = tool_ctx_kitchen_open.run_skill_completion
        assert authority is not None
        tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        invocation_id = authority.begin(
            kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            request_session_id="request-session",
            tracker_order_id="AB",
            tracker_path=str(tracker_path.resolve()),
            tracker_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            tracker_incarnation_id="incarnation",
            step_name="review",
        )
        receipt = authority.draft(
            invocation_id,
            classification="success",
            success=True,
            result_digest="digest",
        )
        authority.publish(receipt.receipt_id)

        def fail_retain(*_args, **_kwargs):
            raise OSError("lease unavailable")

        monkeypatch.setattr(tools_pipeline_tracker, "_retain_context_tracker", fail_retain)
        result = json.loads(
            await complete_run_skill_result(
                receipt.receipt_id,
                ctx=SimpleNamespace(session_id="request-session"),
            )
        )

        assert result["success"] is True
        assert result["tracker_repairable"] is True
        repaired = authority.apply_tracker_credit(
            tracker_order_id="AB",
            tracker_path=str(tracker_path.resolve()),
            tracker_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            tracker_incarnation_id="incarnation",
            step_name="review",
            receipt_id=receipt.receipt_id,
            effect=lambda: {"success": True},
        )
        assert repaired["success"] is True

    @pytest.mark.anyio
    async def test_receipt_tracker_path_must_use_project_authority(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from types import SimpleNamespace

        authority = tool_ctx_kitchen_open.run_skill_completion
        assert authority is not None
        outside_tracker = tmp_path / "outside" / "AB.json"
        invocation_id = authority.begin(
            kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            request_session_id="request-session",
            tracker_order_id="AB",
            tracker_path=str(outside_tracker.resolve()),
            tracker_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            tracker_incarnation_id="incarnation",
            step_name="review",
        )
        receipt = authority.draft(
            invocation_id,
            classification="success",
            success=True,
            result_digest="digest",
        )
        authority.publish(receipt.receipt_id)

        result = json.loads(
            await complete_run_skill_result(
                receipt.receipt_id,
                ctx=SimpleNamespace(session_id="request-session"),
            )
        )

        assert result["success"] is True
        assert result["tracker"]["stage"] == "tracker_credit"
        assert result["tracker_repairable"] is True
        assert not outside_tracker.with_suffix(".lease.lock").exists()


class TestRecordPipelineStepStatus:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_status_returns_current_state(self):
        tracker_dir = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "test-kitchen",
                    "initialized_at": "2026-05-31T01:00:00Z",
                    "steps": {
                        "review": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                        "implement": {"status": "pending"},
                        "verify": {"status": "skipped"},
                    },
                    "dependencies": {"implement": ["review"]},
                }
            )
        )

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="status"))
        assert result["success"] is True
        assert result["complete"] == 1
        assert result["pending"] == 1
        assert result["skipped"] == 1
        assert result["total"] == 3


class TestGetPipelineReportIncludesTrackerGaps:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        tool_ctx_kitchen_open.project_dir = tmp_path
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_get_pipeline_report_includes_tracker_gaps(self):
        from autoskillit.server.tools.tools_status import get_pipeline_report

        tracker_dir = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "test-kitchen",
                    "initialized_at": "2026-05-31T01:00:00Z",
                    "steps": {
                        "review": {"status": "complete"},
                        "implement": {"status": "pending"},
                    },
                    "dependencies": {},
                }
            )
        )

        result = json.loads(await get_pipeline_report())
        assert "step_completion_gaps" in result
        gaps = result["step_completion_gaps"]
        assert any(g["pipeline_id"] == "AB" and g["step"] == "implement" for g in gaps)


def _configure_open_kitchen_mock(ctx, steps, tmp_path):
    from unittest.mock import MagicMock

    ctx.project_dir = tmp_path
    ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        {
            "content": "name: remediation\nsteps: {}\n",
            "valid": True,
            "errors": [],
            "requires_packs": [],
            "requires_features": [],
            "content_hash": "abc",
            "composite_hash": "def",
            "recipe_version": "1.0",
            "suggestions": [],
            "post_prune_step_names": list(steps.keys()),
        }
    )
    mock_recipe_info = MagicMock()
    mock_recipe_info.path = tmp_path / "remediation.yaml"
    ctx.recipes.find.return_value = mock_recipe_info
    mock_recipe_obj = MagicMock()
    mock_recipe_obj.steps = steps
    mock_recipe_obj.ingredients = {}
    ctx.recipes.load.return_value = mock_recipe_obj


class TestOpenKitchenAutoInitTracker:
    def test_auto_init_releases_authority_when_initialization_raises(self, monkeypatch, tmp_path):
        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import _auto_init_pipeline_tracker
        from tests.server.conftest import _make_mock_ctx

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.kitchen_id = "kitchen-error"
        ctx.active_recipe_steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach"),
        }

        def _raise(*_args):
            raise RuntimeError("initialization failed")

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_kitchen.initialize_kitchen_tracker", _raise
        )

        with pytest.raises(RuntimeError, match="initialization failed"):
            _auto_init_pipeline_tracker(ctx)

        assert ctx.tracker_leases == {}
        assert ctx.kitchen_tracker_key is None

    def test_auto_init_preserves_corrupt_authority(self, tmp_path):
        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import (
            _auto_init_pipeline_tracker,
        )
        from tests.server.conftest import _make_mock_ctx

        ctx = _make_mock_ctx()
        ctx.project_dir = tmp_path
        ctx.kitchen_id = "kitchen-corrupt"
        ctx.active_recipe_steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach"),
        }
        tracker_path = (
            tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "kitchen-corrupt.json"
        )
        tracker_path.parent.mkdir(parents=True)
        tracker_path.write_bytes(b"{not-json")
        error = _auto_init_pipeline_tracker(ctx)

        assert error is not None
        assert "invalid" in error
        assert tracker_path.read_bytes() == b"{not-json"
        assert ctx.tracker_leases == {}
        assert ctx.kitchen_tracker_key is None

    @pytest.mark.anyio
    async def test_open_kitchen_auto_inits_tracker(self, tmp_path):
        from unittest.mock import patch

        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import open_kitchen
        from tests.server.conftest import _make_mock_ctx

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="dry_walkthrough"),
        }

        ctx = _make_mock_ctx()
        ctx.gate.enabled = True
        ctx.gate_infrastructure_ready = True
        ctx.recipe_name = ""
        ctx.kitchen_id = "kitchen-abc"
        _set_mock_kitchen_transition(ctx, kitchen_id=ctx.kitchen_id)
        _configure_open_kitchen_mock(ctx, steps, tmp_path)

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            result = json.loads(await open_kitchen(name="remediation", ctx=ctx))

        assert result["success"] is True
        tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "kitchen-abc.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        assert tracker["dependencies"].get("review_approach") == ["rectify"]

    @pytest.mark.anyio
    async def test_open_kitchen_auto_init_idempotent(self, tmp_path):
        from unittest.mock import patch

        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_kitchen import open_kitchen
        from tests.server.conftest import _make_mock_ctx

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="dry_walkthrough"),
        }

        ctx1 = _make_mock_ctx()
        ctx1.gate.enabled = True
        ctx1.gate_infrastructure_ready = True
        ctx1.recipe_name = ""
        ctx1.kitchen_id = "kitchen-abc"
        _set_mock_kitchen_transition(ctx1, kitchen_id=ctx1.kitchen_id)
        _configure_open_kitchen_mock(ctx1, steps, tmp_path)
        with patch("autoskillit.server._get_ctx", return_value=ctx1):
            result1 = json.loads(await open_kitchen(name="remediation", ctx=ctx1))
        assert result1["success"] is True

        tracker_path = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "kitchen-abc.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        tracker["steps"]["rectify"]["status"] = "complete"
        tracker_path.write_text(json.dumps(tracker))

        ctx2 = _make_mock_ctx()
        ctx2.gate.enabled = True
        ctx2.gate_infrastructure_ready = True
        ctx2.recipe_name = "remediation"
        ctx2.kitchen_id = "kitchen-abc"
        _set_mock_kitchen_transition(ctx2, kitchen_id=ctx2.kitchen_id)
        _configure_open_kitchen_mock(ctx2, steps, tmp_path)
        with patch("autoskillit.server._get_ctx", return_value=ctx2):
            result2 = json.loads(await open_kitchen(name="remediation", ctx=ctx2))
        assert result2["success"] is True

        tracker_after = json.loads(tracker_path.read_text())
        assert tracker_after["steps"]["rectify"]["status"] == "complete"
        assert tracker_after["dependencies"].get("review_approach") == ["rectify"]

    @pytest.mark.anyio
    async def test_open_kitchen_auto_init_multi_pipeline(self, tmp_path):
        from unittest.mock import patch

        from autoskillit.recipe.schema import RecipeStep
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps
        from autoskillit.server.tools.tools_kitchen import open_kitchen
        from tests.server.conftest import _make_mock_ctx

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)
        (temp_dir / ".hook_config.json").write_text("{}")

        steps = {
            "rectify": RecipeStep(name="rectify", on_success="review_approach"),
            "review_approach": RecipeStep(name="review_approach", on_success="dry_walkthrough"),
        }

        ctx = _make_mock_ctx()
        ctx.gate.enabled = True
        ctx.gate_infrastructure_ready = True
        ctx.recipe_name = ""
        ctx.kitchen_id = "kitchen-multi"
        _set_mock_kitchen_transition(ctx, kitchen_id=ctx.kitchen_id)
        _configure_open_kitchen_mock(ctx, steps, tmp_path)

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True, exist_ok=True)
        for oid in ("other-pipeline", "second-pipeline"):
            tracker_dir.joinpath(f"{oid}.json").write_text(
                json.dumps(
                    {
                        "pipeline_id": oid,
                        "kitchen_id": "kitchen-multi",
                        "steps": {
                            "rectify": {"status": "complete"},
                            "review_approach": {"status": "pending"},
                        },
                        "dependencies": {"review_approach": ["rectify"]},
                    }
                )
            )

        with patch("autoskillit.server._get_ctx", return_value=ctx):
            result = json.loads(await open_kitchen(name="remediation", ctx=ctx))
            assert result["success"] is True
            tracker_path = tracker_dir / "kitchen-multi.json"
            assert tracker_path.exists()

            from autoskillit.server.tools.tools_execution import _select_tracker_authority

            _target, authority, key, _lease = _select_tracker_authority(ctx, "")
            deny_result = _check_pipeline_deps("review_approach", authority)
            if key is not None:
                from autoskillit.server.tools.tools_pipeline_tracker import (
                    _release_context_tracker,
                )

                _release_context_tracker(ctx, key)

        assert deny_result is not None
        parsed = json.loads(deny_result)
        assert parsed["success"] is False
        assert "Pipeline 'kitchen-multi'" in parsed["error"]
        assert "other-pipeline" not in parsed["error"]


class TestCheckPipelineDepsImmutableTarget:
    @pytest.mark.anyio
    async def test_kitchen_target_ignores_multiple_ambient_pipelines(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools.tools_execution import _check_pipeline_deps

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        for oid in ("AB", "CD"):
            tracker_dir.joinpath(f"{oid}.json").write_text(
                json.dumps(
                    {
                        "pipeline_id": oid,
                        "kitchen_id": "kitchen-xyz",
                        "steps": {"a": {"status": "pending"}, "b": {"status": "pending"}},
                        "dependencies": {"b": ["a"]},
                    }
                )
            )

        from autoskillit.server.tools.tools_execution import _select_tracker_authority

        _target, authority, key, _lease = _select_tracker_authority(
            tool_ctx_kitchen_open,
            "",
        )
        result = _check_pipeline_deps("b", authority)
        assert _target is not None
        assert _target.target_order_id == "kitchen-xyz"
        if key is not None:
            from autoskillit.server.tools.tools_pipeline_tracker import (
                _release_context_tracker,
            )

            _release_context_tracker(tool_ctx_kitchen_open, key)
        assert result is None


class TestSelectTrackerAuthority:
    def test_read_failure_releases_retained_lease(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from autoskillit.server.tools import tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        retained = {}

        def fail_read(_target, lease):
            retained["lease"] = lease
            raise OSError("read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_authority", fail_read)

        with pytest.raises(OSError, match="read failed"):
            tools_pipeline_tracker._select_tracker_authority(tool_ctx_kitchen_open, "")

        assert retained["lease"].closed
        assert tool_ctx_kitchen_open.tracker_leases == {}

    def test_scoped_selection_cannot_release_kitchen_lifetime_lease(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        from autoskillit.server.tools import tools_kitchen, tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        kitchen_key, kitchen_lease = tools_kitchen._retain_kitchen_tracker_authority(
            tool_ctx_kitchen_open
        )

        _target, _authority, scoped_key, scoped_lease = (
            tools_pipeline_tracker._select_tracker_authority(tool_ctx_kitchen_open, "")
        )
        assert scoped_key is not None
        assert scoped_lease is not None
        assert scoped_key != kitchen_key

        tools_pipeline_tracker._release_context_tracker(tool_ctx_kitchen_open, scoped_key)
        assert not kitchen_lease.closed
        assert tool_ctx_kitchen_open.tracker_leases == {kitchen_key: kitchen_lease}
        tools_kitchen._release_kitchen_tracker_authority(
            tool_ctx_kitchen_open, unregister=False, retire=False
        )

    def test_completion_binding_read_exception_releases_lease(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from autoskillit.core import TrackerAuthorityTarget
        from autoskillit.server.tools import tools_execution, tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=True)
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(json.dumps({"steps": {}, "dependencies": {}}))

        def fail_read(_target, _lease):
            raise OSError("identity read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_identity", fail_read)
        with pytest.raises(OSError, match="identity read failed"):
            tools_execution._completion_tracker_binding(
                tool_ctx_kitchen_open, "AB", tracker_target=target
            )

        assert tool_ctx_kitchen_open.tracker_leases == {}


class TestRestoreReservedTrackerAuthority:
    def test_same_participant_keeps_existing_lease(self, tool_ctx_kitchen_open, tmp_path):
        from types import SimpleNamespace
        from typing import cast

        from autoskillit.core import AuditIdentityReservation, TrackerAuthorityTarget
        from autoskillit.server.tools.tools_pipeline_tracker import (
            _release_context_tracker,
            _restore_reserved_tracker_authority,
            _retain_context_tracker,
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=True)
        key, lease = _retain_context_tracker(
            tool_ctx_kitchen_open,
            target,
            owner_kind="kitchen",
            owner_id="kitchen-xyz",
        )
        reservation = cast(
            AuditIdentityReservation,
            SimpleNamespace(tracker_target_order_id="AB", tracker_expected=True),
        )

        _target, _authority, restored_key, restored_lease = _restore_reserved_tracker_authority(
            tool_ctx_kitchen_open,
            reservation,
            key,
        )

        assert restored_key == key
        assert restored_lease is lease
        assert not lease.closed
        assert list(tool_ctx_kitchen_open.tracker_leases) == [key]
        _release_context_tracker(tool_ctx_kitchen_open, key)

    def test_replacement_read_failure_preserves_current_lease(
        self, tool_ctx_kitchen_open, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace
        from typing import cast

        from autoskillit.core import AuditIdentityReservation, TrackerAuthorityTarget
        from autoskillit.server.tools import tools_pipeline_tracker

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"
        current_target = TrackerAuthorityTarget.for_project(tmp_path, "AB", expected=True)
        current_key, current_lease = tools_pipeline_tracker._retain_context_tracker(
            tool_ctx_kitchen_open,
            current_target,
            owner_kind="kitchen",
            owner_id="kitchen-xyz",
        )
        reservation = cast(
            AuditIdentityReservation,
            SimpleNamespace(tracker_target_order_id="CD", tracker_expected=True),
        )

        def fail_read(_target, _lease):
            raise OSError("read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_authority", fail_read)

        with pytest.raises(OSError, match="read failed"):
            tools_pipeline_tracker._restore_reserved_tracker_authority(
                tool_ctx_kitchen_open,
                reservation,
                current_key,
            )

        assert not current_lease.closed
        assert tool_ctx_kitchen_open.tracker_leases == {current_key: current_lease}
        tools_pipeline_tracker._release_context_tracker(tool_ctx_kitchen_open, current_key)


class TestSelectTrackerTarget:
    def test_kitchen_scoped_fallback_never_scans_ambient_candidates(
        self, tool_ctx_kitchen_open, tmp_path
    ):
        """The caller-selected kitchen target is immutable despite ambient files."""
        from autoskillit.server.tools.tools_pipeline_tracker import (
            select_tracker_target,
        )

        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "kitchen-xyz"

        tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "kitchen-xyz",
                    "steps": {"a": {"status": "pending"}},
                    "dependencies": {},
                }
            )
        )

        result = select_tracker_target(tool_ctx_kitchen_open, "", expected=False)

        assert result is not None
        assert result.target_order_id == "kitchen-xyz"
        assert result.path == tracker_dir / "kitchen-xyz.json"
