"""Shared plan-set materialization fixtures for server tests."""

from pathlib import Path

import pytest

from autoskillit.core import resolve_temp_dir
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer

PLAN_SET_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plan_set" / "split_r1_r2"


class PlanSetGitHub:
    def __init__(self, *, success: bool = True, fixture_dir: Path = PLAN_SET_FIXTURE) -> None:
        self.success = success
        self.fixture_dir = fixture_dir

    async def fetch_issue(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        if not self.success:
            return {"success": False, "error": "offline"}
        return {
            "success": True,
            "issue_number": 1,
            "body": (self.fixture_dir / "issue.md").read_text(),
        }


def write_plan_set_parts(
    tmp_path: Path,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixture_dir: Path = PLAN_SET_FIXTURE,
) -> tuple[Path, Path, Path]:
    root = resolve_temp_dir(tmp_path, tool_ctx.config.workspace.temp_dir)
    root.mkdir(parents=True, exist_ok=True)
    parts = (root / "part_a.md", root / "part_b.md")
    for part in parts:
        part.write_text((fixture_dir / part.name).read_text(), encoding="utf-8")
    monkeypatch.setattr(
        tool_ctx,
        "plan_set_materializer",
        DefaultPlanSetMaterializer(PlanSetGitHub(fixture_dir=fixture_dir)),
    )
    return root, *parts
