"""Tests for resume_ownership_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _run_guard(
    tool_input: dict | None = None,
    *,
    headless: bool = False,
    raw_stdin: str | None = None,
    session_id: str = "",
    campaign_id: str = "",
    state_dir: str = "",
) -> str:
    from autoskillit.hooks.guards.resume_ownership_guard import main

    payload: dict[str, object] = {"tool_input": tool_input or {}}
    if session_id:
        payload["session_id"] = session_id
    stdin_content = raw_stdin if raw_stdin is not None else json.dumps(payload)

    env_updates: dict[str, str] = {}
    if headless:
        env_updates["AUTOSKILLIT_HEADLESS"] = "1"
    if campaign_id:
        env_updates["AUTOSKILLIT_CAMPAIGN_ID"] = campaign_id
    if state_dir:
        env_updates["AUTOSKILLIT_STATE_DIR"] = state_dir

    env_removals = []
    if not headless:
        env_removals.append("AUTOSKILLIT_HEADLESS")

    with (
        patch.dict(os.environ, env_updates, clear=False),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        for key in env_removals:
            os.environ.pop(key, None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    raise
        return buf.getvalue()


def _write_provenance(path, records: list[dict]) -> None:
    lines = [json.dumps(r) for r in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


class TestResumeOwnershipGuard:
    def test_non_headless_passes_through(self) -> None:
        out = _run_guard(
            {"resume_session_id": "sess-1"},
            headless=False,
        )
        assert not out.strip()

    def test_no_resume_session_id_passes_through(self) -> None:
        out = _run_guard(
            {"some_other_field": "value"},
            headless=True,
        )
        assert not out.strip()

    def test_empty_resume_session_id_passes_through(self) -> None:
        out = _run_guard(
            {"resume_session_id": ""},
            headless=True,
        )
        assert not out.strip()

    def test_no_provenance_record_passes_through(self, tmp_path: Path) -> None:
        out = _run_guard(
            {"resume_session_id": "unknown-sess"},
            headless=True,
            state_dir=str(tmp_path),
        )
        assert not out.strip()

    def test_non_food_truck_session_denied(self, tmp_path: Path) -> None:
        prov_path = tmp_path / "session_provenance.jsonl"
        _write_provenance(prov_path, [{"session_id": "sess-1", "recipe_name": ""}])
        out = _run_guard(
            {"resume_session_id": "sess-1"},
            headless=True,
            state_dir=str(tmp_path),
        )
        response = json.loads(out)
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "not a food truck" in response["hookSpecificOutput"]["permissionDecisionReason"]

    def test_matching_caller_session_id_passes(self, tmp_path: Path) -> None:
        prov_path = tmp_path / "session_provenance.jsonl"
        _write_provenance(
            prov_path,
            [
                {
                    "session_id": "sess-target",
                    "recipe_name": "test-recipe",
                    "caller_session_id": "my-caller",
                    "kitchen_id": "kitchen-1",
                }
            ],
        )
        out = _run_guard(
            {"resume_session_id": "sess-target"},
            headless=True,
            state_dir=str(tmp_path),
            session_id="my-caller",
        )
        assert not out.strip()

    def test_matching_kitchen_id_passes(self, tmp_path: Path) -> None:
        prov_path = tmp_path / "session_provenance.jsonl"
        _write_provenance(
            prov_path,
            [
                {
                    "session_id": "sess-target",
                    "recipe_name": "test-recipe",
                    "caller_session_id": "other-caller",
                    "kitchen_id": "my-kitchen",
                }
            ],
        )
        out = _run_guard(
            {"resume_session_id": "sess-target"},
            headless=True,
            state_dir=str(tmp_path),
            session_id="different-caller",
            campaign_id="my-kitchen",
        )
        assert not out.strip()

    def test_non_matching_ownership_denied(self, tmp_path: Path) -> None:
        prov_path = tmp_path / "session_provenance.jsonl"
        _write_provenance(
            prov_path,
            [
                {
                    "session_id": "sess-target",
                    "recipe_name": "test-recipe",
                    "caller_session_id": "owner-caller",
                    "kitchen_id": "owner-kitchen",
                }
            ],
        )
        out = _run_guard(
            {"resume_session_id": "sess-target"},
            headless=True,
            state_dir=str(tmp_path),
            session_id="wrong-caller",
            campaign_id="wrong-kitchen",
        )
        response = json.loads(out)
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "not owned" in response["hookSpecificOutput"]["permissionDecisionReason"]

    def test_malformed_input_fails_open(self) -> None:
        out = _run_guard(raw_stdin="not-json", headless=True)
        assert not out.strip()

    def test_malformed_provenance_lines_skipped(self, tmp_path: Path) -> None:
        prov_path = tmp_path / "session_provenance.jsonl"
        prov_path.parent.mkdir(parents=True, exist_ok=True)
        prov_path.write_text(
            "not-json\n"
            + json.dumps(
                {
                    "session_id": "sess-1",
                    "recipe_name": "r",
                    "caller_session_id": "my-caller",
                    "kitchen_id": "k",
                }
            )
            + "\n"
        )
        out = _run_guard(
            {"resume_session_id": "sess-1"},
            headless=True,
            state_dir=str(tmp_path),
            session_id="my-caller",
        )
        assert not out.strip()
