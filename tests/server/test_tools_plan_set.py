"""The public bind_plan_set wrapper remains headless and returns bounded failures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import autoskillit.server._plan_set_materializer as materializer_module
from autoskillit.core import PlanSetRejectReason, resolve_temp_dir
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.tools.tools_plan_set import bind_plan_set

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plan_set" / "split_r1_r2"


class _GitHub:
    def __init__(self, *, success: bool = True) -> None:
        self.success = success

    async def fetch_issue(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        if not self.success:
            return {"success": False, "error": "offline"}
        return {"success": True, "issue_number": 1, "body": (_FIXTURE / "issue.md").read_text()}


def _parts(tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    root = resolve_temp_dir(tmp_path, tool_ctx.config.workspace.temp_dir)
    root.mkdir(parents=True, exist_ok=True)
    parts = (root / "part_a.md", root / "part_b.md")
    for part in parts:
        part.write_text((_FIXTURE / part.name).read_text(), encoding="utf-8")
    monkeypatch.setattr(tool_ctx, "plan_set_materializer", DefaultPlanSetMaterializer(_GitHub()))
    return root, *parts


async def test_headless_bind_writes_snapshot_and_tracks_response(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first, second = _parts(tmp_path, tool_ctx, monkeypatch)
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


async def test_open_bind_and_mixed_parent_list(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, first, second = _parts(tmp_path, tool_ctx, monkeypatch)
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
    _, first, second = _parts(tmp_path, tool_ctx, monkeypatch)

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


@pytest.mark.parametrize("case", ("empty", "outside", "fetch_failure", "no_issue"))
async def test_wrapper_classifies_inputs_and_failures(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root, first, _ = _parts(tmp_path, tool_ctx, monkeypatch)
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
            tool_ctx, "plan_set_materializer", DefaultPlanSetMaterializer(_GitHub(success=False))
        )
        result = json.loads(
            await bind_plan_set(plan_parts=str(first), cwd=str(tmp_path), issue_url="owner/repo#1")
        )
        assert result["reason"] == PlanSetRejectReason.ISSUE_FETCH_FAILED.value
        assert not list((root / "plan-set-authority").rglob("*.json"))
    else:
        first.write_text(
            "# Plan\n\n### Step 1.1: P-1\nImplement P-1.\n\n"
            "## Issue Requirement Allocation\n"
            "| Requirement ID | Allocation | Implementation Step |\n"
            "|---|---|---|\n| P-1 | owned | Step 1.1 |\n",
            encoding="utf-8",
        )
        result = json.loads(await bind_plan_set(plan_parts=str(first), cwd=str(tmp_path)))
        assert result["success"] is True
        authority = json.loads(Path(result["plan_set_authority_path"]).read_text())
        assert authority["inventory_mode"] == "no_issue"
