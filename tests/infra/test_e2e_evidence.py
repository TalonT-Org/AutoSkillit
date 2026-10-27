"""Behavioral checks for the captured E2E evidence helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.infra._e2e_evidence import _PROJECTS_TOKEN, _replace_projects_token

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def test_replace_projects_token_json_escapes_paths(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    projects = Path(f'{tmp_path}/project "quoted" path\\with\\backslashes')
    trace.write_text(
        json.dumps({"transcript": f"{_PROJECTS_TOKEN}/session.jsonl"}),
        encoding="utf-8",
    )

    _replace_projects_token(trace, projects)

    assert json.loads(trace.read_text(encoding="utf-8")) == {
        "transcript": f"{projects}/session.jsonl"
    }
