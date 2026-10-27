"""The public bind_plan_set wrapper remains headless and returns bounded failures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import autoskillit.server._plan_set_materializer as materializer_module
from autoskillit.core import (
    PlanSetBindRequest,
    PlanSetBindResult,
    PlanSetRejectReason,
    resolve_temp_dir,
)
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.tools.tools_plan_set import bind_plan_set
from tests.server._plan_set_test_helpers import (
    PLAN_SET_FIXTURE,
    PlanSetGitHub,
    write_plan_set_parts,
)
from tests.server._recipe_fixture_parameters import (
    IMPLEMENTATION_OVERRIDES,
    IMPLEMENTATION_READY_PARAMETERS,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_MISSING_R2_FIXTURE = PLAN_SET_FIXTURE.parent / "missing_r2"
_PUBLICATION_FIXTURE = PLAN_SET_FIXTURE.parent / "publication_pr"


class _RecordingMaterializer:
    def __init__(self, delegate: DefaultPlanSetMaterializer) -> None:
        self.delegate = delegate
        self.request: PlanSetBindRequest | None = None

    async def bind(self, request: PlanSetBindRequest) -> PlanSetBindResult:
        self.request = request
        return await self.delegate.bind(request)

    def lookup_binding(
        self, execution_generation: str, authority_path: str
    ) -> tuple[Path, str] | None:
        return self.delegate.lookup_binding(execution_generation, authority_path)


async def test_headless_bind_writes_snapshot_and_tracks_response(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first, second = write_plan_set_parts(tmp_path, tool_ctx, monkeypatch)
    assert not tool_ctx.gate.enabled
    encoded = await bind_plan_set(
        plan_parts=f"{first},{second}\n",
        cwd=str(tmp_path),
        issue_url="owner/repo#1",
    )
    result = json.loads(encoded)
    assert result["success"] is True
    assert result["plan_set_state"] == "sealed"
    assert result["plan_set_parts"] == f"{first}\n{second}"
    assert Path(result["plan_set_authority_path"]).is_file()
    assert len(list((root / "plan-set-authority").rglob("issue.*.md"))) == 1
    assert any(row["tool_name"] == "bind_plan_set" for row in tool_ctx.response_log.get_report())

    second.write_text(
        second.read_text().replace(
            "## Issue Requirement Allocation",
            "Walkthrough clarification for R2.\n\n## Issue Requirement Allocation",
        ),
        encoding="utf-8",
    )
    renewed = json.loads(
        await bind_plan_set(
            plan_parts=f"{first}\n{second}",
            cwd=str(tmp_path),
            parent_authority_path=result["plan_set_authority_path"],
        )
    )
    assert renewed["success"] is True
    assert renewed["plan_set_authority_digest"] != result["plan_set_authority_digest"]


@pytest.mark.parametrize(
    ("open_pr", "auto_merge", "expected_open_pr", "expected_auto_merge", "success"),
    [
        ("", "false", False, False, False),
        ("false", "false", False, False, False),
        ("0", "false", False, False, False),
        ("no", "false", False, False, False),
        ("off", "false", True, False, True),
        ("true", "true", True, True, False),
        ("true", "TRUE", True, False, True),
        ("true", "yes", True, False, True),
    ],
)
async def test_bind_plan_set_passes_publication_flags_to_materializer(
    tmp_path: Path,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    open_pr: str,
    auto_merge: str,
    expected_open_pr: bool,
    expected_auto_merge: bool,
    success: bool,
) -> None:
    root = resolve_temp_dir(tmp_path, tool_ctx.config.workspace.temp_dir)
    root.mkdir(parents=True, exist_ok=True)
    part = root / "part_a.md"
    part.write_text((_PUBLICATION_FIXTURE / "part_a.md").read_text(), encoding="utf-8")
    recorder = _RecordingMaterializer(
        DefaultPlanSetMaterializer(PlanSetGitHub(fixture_dir=_PUBLICATION_FIXTURE))
    )
    monkeypatch.setattr(tool_ctx, "plan_set_materializer", recorder)

    result = json.loads(
        await bind_plan_set(
            plan_parts=str(part),
            cwd=str(tmp_path),
            issue_url="owner/repo#1",
            open_pr=open_pr,
            auto_merge=auto_merge,
            prior_authority_path="",
        )
    )

    assert recorder.request is not None
    assert recorder.request.open_pr is expected_open_pr
    assert recorder.request.auto_merge is expected_auto_merge
    assert recorder.request.prior_authority_path == ""
    assert result["success"] is success
    if success:
        assert result["reason"] == ""
    else:
        assert result["reason"] == PlanSetRejectReason.STAGE_OBLIGATION_UNAVAILABLE.value


def test_stage_obligation_unavailability_falls_through_to_failure_in_every_bind_recipe_step() -> (
    None
):
    recipe_steps = {
        "implementation": {"bind_plan_set", "renew_plan_set"},
        "remediation": {"bind_plan_set", "renew_before_retry", "renew_plan_set"},
        "implementation-groups": {"bind_group_parts", "seal_plan_set", "renew_plan_set"},
    }

    for recipe_name, expected_steps in recipe_steps.items():
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        bind_steps = {
            name: step for name, step in recipe.steps.items() if step.tool == "bind_plan_set"
        }
        assert set(bind_steps) == expected_steps
        for step_name, step in bind_steps.items():
            assert step.on_result is not None, (recipe_name, step_name)
            conditions = step.on_result.conditions
            replan_routes = [
                condition
                for condition in conditions
                if condition.route == "check_replan_iteration"
            ]
            assert all(
                condition.when is not None and "coverage_failed" in condition.when
                for condition in replan_routes
            ), (recipe_name, step_name)
            assert any(
                condition.when is None and condition.route == "release_issue_failure"
                for condition in conditions
            ), (recipe_name, step_name)


async def test_open_bind_and_mixed_parent_list(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, first, second = write_plan_set_parts(tmp_path, tool_ctx, monkeypatch)
    opened = json.loads(
        await bind_plan_set(plan_parts=str(first), cwd=str(tmp_path), seal="false")
    )
    assert opened["success"] is True
    assert opened["plan_set_state"] == "open"
    assert opened["coverage_status"] == "not_evaluated"
    mixed = json.loads(
        await bind_plan_set(
            plan_parts=f"{first}\n{second}",
            cwd=str(tmp_path),
            parent_authority_path=opened["plan_set_authority_path"],
        )
    )
    assert mixed["reason"] == PlanSetRejectReason.PART_LIST_CHANGED.value


async def test_identical_bind_reconciles_existing_artifacts(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, first, second = write_plan_set_parts(tmp_path, tool_ctx, monkeypatch)

    class FrozenDatetime:
        @staticmethod
        def now(_zone):
            return datetime(2026, 9, 18, tzinfo=UTC)

    monkeypatch.setattr(materializer_module, "datetime", FrozenDatetime)
    args = {
        "plan_parts": f"{first}\n{second}",
        "cwd": str(tmp_path),
        "issue_url": "owner/repo#1",
    }
    initial = json.loads(await bind_plan_set(**args))
    repeated = json.loads(await bind_plan_set(**args))
    assert initial["success"] and repeated["success"]
    assert repeated["plan_set_authority_path"] == initial["plan_set_authority_path"]


@pytest.mark.parametrize(
    "case", ("empty", "outside", "fetch_failure", "no_issue", "coverage_failed")
)
async def test_wrapper_classifies_inputs_and_failures(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root, first, _ = write_plan_set_parts(tmp_path, tool_ctx, monkeypatch)
    if case == "empty":
        result = json.loads(await bind_plan_set(plan_parts=" ,\n", cwd=str(tmp_path)))
        assert result["reason"] == PlanSetRejectReason.NO_PARTS.value
    elif case == "outside":
        outside = tmp_path / "outside.md"
        outside.write_text(first.read_text(), encoding="utf-8")
        result = json.loads(await bind_plan_set(plan_parts=str(outside), cwd=str(tmp_path)))
        assert result["reason"] == PlanSetRejectReason.CONTAINMENT.value
    elif case == "fetch_failure":
        monkeypatch.setattr(
            tool_ctx,
            "plan_set_materializer",
            DefaultPlanSetMaterializer(PlanSetGitHub(success=False)),
        )
        result = json.loads(
            await bind_plan_set(plan_parts=str(first), cwd=str(tmp_path), issue_url="owner/repo#1")
        )
        assert result["reason"] == PlanSetRejectReason.ISSUE_FETCH_FAILED.value
        assert not list((root / "plan-set-authority").rglob("*.json"))
    else:
        step_heading = "#### Step 1.1: P-1" if case == "coverage_failed" else "### Step 1.1: P-1"
        first.write_text(
            f"# Plan\n\n{step_heading}\nImplement P-1.\n\n"
            "## Issue Requirement Allocation\n"
            "| Requirement ID | Allocation | Implementation Step |\n"
            "|---|---|---|\n| P-1 | owned | Step 1.1 |\n",
            encoding="utf-8",
        )
        result = json.loads(await bind_plan_set(plan_parts=str(first), cwd=str(tmp_path)))
        if case == "coverage_failed":
            assert result["reason"] == PlanSetRejectReason.COVERAGE_FAILED.value
            assert result["coverage_gaps"].strip()
            return
        assert result["success"] is True
        authority = json.loads(Path(result["plan_set_authority_path"]).read_text())
        assert authority["inventory_mode"] == "no_issue"


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    IMPLEMENTATION_READY_PARAMETERS,
    indirect=True,
)
async def test_bind_plan_set_coverage_failure_delivers_recovery(
    tool_ctx_ready_recipe,
    git_checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.server._recipe_segment_test_helpers import assert_delivery_outcome

    _, first, second = write_plan_set_parts(
        git_checkout,
        tool_ctx_ready_recipe.tool_ctx,
        monkeypatch,
        fixture_dir=_MISSING_R2_FIXTURE,
    )
    result = json.loads(
        await bind_plan_set(
            step_name="bind_plan_set",
            plan_parts=f"{first}\n{second}",
            cwd=str(git_checkout),
            issue_url=IMPLEMENTATION_OVERRIDES["issue_url"],
        )
    )

    assert result["reason"] == PlanSetRejectReason.COVERAGE_FAILED.value
    assert_delivery_outcome("bind_plan_set", result, expected=False)
    segment = result["recipe_segment"]
    assert segment["kind"] == "recovery"
    assert "release_issue_failure" in segment["target_steps"]
