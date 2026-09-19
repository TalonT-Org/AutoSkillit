"""Implementation admission binds the current part to a sealed local authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetPreflightRequest,
    PlanSetRejectReason,
    RecipeExecutionId,
)
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.lifecycle._guards import _check_dry_walkthrough_plan
from autoskillit.server.recipe._plan_set_preflight import DefaultPlanSetPreflightResolver
from tests.server.test_plan_set_materializer import _FIXTURES, _GitHub

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]


async def _bind(
    root: Path,
    *,
    kitchen: str = "kitchen",
    generation: str = "generation",
    mode: PlanSetBindingMode = PlanSetBindingMode.RECIPE,
    seal: bool = True,
    parent: str = "",
    write_parts: bool = True,
):
    root.mkdir(parents=True, exist_ok=True)
    fixture = _FIXTURES / "split_r1_r2"
    parts = (root / "part_a.md", root / "part_b.md")
    if write_parts:
        for part in parts:
            part.write_text((fixture / part.name).read_text(), encoding="utf-8")
    materializer = DefaultPlanSetMaterializer(_GitHub())
    bound = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw="\n".join(str(part) for part in parts),
            allowed_root=root,
            issue_url="owner/repo#1" if not parent else "",
            parent_authority_path=parent,
            seal=seal,
            step_name="bind" if not parent else "renew",
            execution_generation=generation,
            kitchen_id=kitchen,
            dispatch_id="",
            binding_mode=mode,
        )
    )
    assert bound.success
    return bound, parts, materializer


def _preflight(
    authority_path: str,
    plan: Path,
    root: Path,
    *,
    kitchen: str = "kitchen",
    generation: str = "generation",
):
    resolver = DefaultPlanSetPreflightResolver(RecipeExecutionId(generation), kitchen)
    return resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation=generation,
            expected_kitchen_id=kitchen,
            step_name="implement",
            skill_name="implement-worktree-no-merge",
            plan_path=str(plan),
            plan_set_authority_path=authority_path,
        ),
        allowed_root=root,
    )


async def test_unchanged_part_admits_only_its_assigned_requirements(tmp_path: Path) -> None:
    bound, parts, _ = await _bind(tmp_path)
    result = _preflight(bound.plan_set_authority_path, parts[0], tmp_path)
    assert result.accepted and result.evidence is not None
    assert [row.requirement_id for row in result.evidence.assigned_requirements] == ["R1"]


async def test_walkthrough_edit_requires_renewal(tmp_path: Path) -> None:
    bound, parts, materializer = await _bind(tmp_path)
    parts[0].write_text(
        parts[0]
        .read_text()
        .replace(
            "## Issue Requirement Allocation",
            "Walkthrough clarification for R1.\n\n## Issue Requirement Allocation",
        )
    )
    assert _preflight(bound.plan_set_authority_path, parts[0], tmp_path).reason is (
        PlanSetRejectReason.PART_CONTENT_CHANGED
    )
    renewed = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw="\n".join(str(part) for part in parts),
            allowed_root=tmp_path,
            issue_url="",
            parent_authority_path=bound.plan_set_authority_path,
            seal=True,
            step_name="renew",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )
    assert renewed.success
    assert _preflight(renewed.plan_set_authority_path, parts[0], tmp_path).accepted


async def test_foreign_unsealed_and_standalone_authorities_never_admit(tmp_path: Path) -> None:
    bound, parts, _ = await _bind(tmp_path / "one")
    other = tmp_path / "other"
    other.mkdir()
    assert _preflight(bound.plan_set_authority_path, parts[0], other).reason is (
        PlanSetRejectReason.PATH_ESCAPE
    )
    assert _preflight(
        bound.plan_set_authority_path, parts[0], tmp_path / "one", generation="other"
    ).reason is (PlanSetRejectReason.EXECUTION_GENERATION)
    opened, open_parts, _ = await _bind(tmp_path / "open", seal=False)
    assert _preflight(opened.plan_set_authority_path, open_parts[0], tmp_path / "open").reason is (
        PlanSetRejectReason.AUTHORITY_NOT_SEALED
    )
    standalone, standalone_parts, _ = await _bind(
        tmp_path / "standalone", mode=PlanSetBindingMode.STANDALONE
    )
    assert (
        _preflight(
            standalone.plan_set_authority_path, standalone_parts[0], tmp_path / "standalone"
        ).reason
        is PlanSetRejectReason.BINDING_MODE
    )


async def test_two_kitchens_cannot_consume_each_others_authority(tmp_path: Path) -> None:
    first, parts, _ = await _bind(tmp_path, kitchen="first")
    second, _, _ = await _bind(tmp_path, kitchen="second", write_parts=False)
    assert first.plan_set_authority_id != second.plan_set_authority_id
    assert _preflight(
        first.plan_set_authority_path, parts[0], tmp_path, kitchen="second"
    ).reason is (PlanSetRejectReason.KITCHEN_ID)


async def test_resumed_execution_consumes_original_authority_without_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound, parts, materializer = await _bind(tmp_path)

    async def unexpected_bind(_request: object) -> None:
        raise AssertionError("resume must not re-bind an existing authority")

    monkeypatch.setattr(materializer, "bind", unexpected_bind)
    original_id = RecipeExecutionId("generation")
    resumed_id = RecipeExecutionId("generation")
    assert original_id == resumed_id
    resumed = DefaultPlanSetPreflightResolver(resumed_id, "kitchen")
    admitted = resumed.resolve(
        PlanSetPreflightRequest(
            execution_generation=resumed_id.value,
            expected_kitchen_id="kitchen",
            step_name="implement",
            skill_name="implement-worktree-no-merge",
            plan_path=str(parts[0]),
            plan_set_authority_path=bound.plan_set_authority_path,
        ),
        allowed_root=tmp_path,
    )
    assert admitted.accepted


async def test_marker_gate_precedes_digest_admission(tmp_path: Path, tool_ctx) -> None:
    root = tmp_path / "make-plan"
    bound, parts, _ = await _bind(root)
    skill = next(name for name in tool_ctx.config.implement_gate.skill_names if "no-merge" in name)
    unmarked = _check_dry_walkthrough_plan(
        skill, str(tmp_path), str(parts[0]), config=tool_ctx.config
    )
    assert unmarked is not None
    assert "dry-walked" in json.loads(unmarked)["result"].lower()

    parts[0].write_text("Dry-walkthrough verified = TRUE\n" + parts[0].read_text())
    assert (
        _check_dry_walkthrough_plan(skill, str(tmp_path), str(parts[0]), config=tool_ctx.config)
        is None
    )
    assert _preflight(bound.plan_set_authority_path, parts[0], root).reason is (
        PlanSetRejectReason.PART_CONTENT_CHANGED
    )
