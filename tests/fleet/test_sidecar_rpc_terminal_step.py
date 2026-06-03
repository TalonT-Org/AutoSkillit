"""Tests for write_sidecar_entry terminal_step parameter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.fleet._sidecar_rpc import get_remaining_issues, write_sidecar_entry
from autoskillit.fleet.sidecar import sidecar_path

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

URL = "https://github.com/org/repo/issues/1"
DISPATCH_ID = "test-dispatch"


class TestWriteSidecarEntryTerminalStep:
    def test_accepts_terminal_step(self, tmp_path: Path) -> None:
        result = write_sidecar_entry(
            dispatch_id=DISPATCH_ID,
            issue_url=URL,
            status="completed",
            pr_url="https://github.com/org/repo/pull/1",
            terminal_step="escalate_stop",
            project_dir=str(tmp_path),
        )
        assert result["ok"] == "true"
        path = sidecar_path(DISPATCH_ID, tmp_path)
        data = json.loads(path.read_text().strip())
        assert data["terminal_step"] == "escalate_stop"

    def test_omits_terminal_step_when_empty(self, tmp_path: Path) -> None:
        write_sidecar_entry(
            dispatch_id=DISPATCH_ID,
            issue_url=URL,
            status="completed",
            project_dir=str(tmp_path),
        )
        path = sidecar_path(DISPATCH_ID, tmp_path)
        data = json.loads(path.read_text().strip())
        assert "terminal_step" not in data

    def test_get_remaining_issues_works_with_terminal_step(self, tmp_path: Path) -> None:
        url2 = "https://github.com/org/repo/issues/2"
        write_sidecar_entry(
            dispatch_id=DISPATCH_ID,
            issue_url=URL,
            status="completed",
            terminal_step="done",
            project_dir=str(tmp_path),
        )
        result = get_remaining_issues(
            dispatch_id=DISPATCH_ID,
            original_urls_json=json.dumps([URL, url2]),
            project_dir=str(tmp_path),
        )
        remaining = json.loads(result["remaining_urls_json"])
        assert remaining == [url2]
