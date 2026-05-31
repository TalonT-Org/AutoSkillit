"""Tests for ingredient_lock_guard.py PreToolUse hook."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

SCRIPT = (
    Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/guards/ingredient_lock_guard.py"
)


def _run(stdin_data: str, env: dict | None = None, cwd: Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
    return result.returncode, result.stdout


class TestIngredientLockGuardDeniesLockedStep:
    """Test 9: ingredient_lock_guard denies locked step."""

    def test_ingredient_lock_guard_denies_locked_step(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"": {"investigate": False}},
                    "locked_ingredients": {"": {"investigate": "false"}},
                }
            )
        )

        # Change to tmp_path so _hook_settings reads from there
        monkeypatch.chdir(tmp_path)

        event = json.dumps(
            {
                "tool_input": {
                    "step_name": "investigate",
                    "order_id": "",
                }
            }
        )

        code, stdout = _run(event)
        assert code == 0

        decision = json.loads(stdout)
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "INGREDIENT LOCK" in decision["hookSpecificOutput"]["permissionDecisionReason"]


class TestIngredientLockGuardAllowsUnlockedStep:
    """Test 10: ingredient_lock_guard allows unlocked step."""

    def test_ingredient_lock_guard_allows_unlocked_step(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps({"locked_steps": {}})  # No locked steps
        )

        monkeypatch.chdir(tmp_path)

        event = json.dumps(
            {
                "tool_input": {
                    "step_name": "review",
                    "order_id": "",
                }
            }
        )

        code, stdout = _run(event)
        assert code == 0
        # No output means allow (fail-open)
        assert stdout.strip() == ""


class TestIngredientLockGuardFailOpen:
    """Test 11: ingredient_lock_guard fail-open on malformed input."""

    def test_ingredient_lock_guard_fail_open_empty_stdin(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, stdout = _run("")
        assert code == 0

    def test_ingredient_lock_guard_fail_open_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, stdout = _run("not json")
        assert code == 0

    def test_ingredient_lock_guard_fail_open_missing_overlay(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        event = json.dumps({"tool_input": {"step_name": "investigate", "order_id": ""}})
        code, stdout = _run(event)
        assert code == 0


class TestIngredientLockGuardPipelineScoped:
    """Test 12: ingredient_lock_guard pipeline-scoped enforcement."""

    def test_ingredient_lock_guard_pipeline_a_denied(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {
                        "a": {"investigate": False},
                        "b": {},
                    },
                    "locked_ingredients": {"a": {"investigate": "false"}},
                }
            )
        )

        monkeypatch.chdir(tmp_path)

        event = json.dumps(
            {
                "tool_input": {
                    "step_name": "investigate",
                    "order_id": "a",
                }
            }
        )

        code, stdout = _run(event)
        assert code == 0
        decision = json.loads(stdout)
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_ingredient_lock_guard_pipeline_b_allowed(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {
                        "a": {"investigate": False},
                        "b": {},
                    },
                }
            )
        )

        monkeypatch.chdir(tmp_path)

        event = json.dumps(
            {
                "tool_input": {
                    "step_name": "investigate",
                    "order_id": "b",
                }
            }
        )

        code, stdout = _run(event)
        assert code == 0
        assert stdout.strip() == ""

    def test_ingredient_lock_guard_env_dispatch_id(self, tmp_path, monkeypatch):
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_dir.mkdir(parents=True)

        overlay = temp_dir / ".hook_config_overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "locked_steps": {"pipeline-x": {"investigate": False}},
                    "locked_ingredients": {"pipeline-x": {"investigate": "false"}},
                }
            )
        )

        event = json.dumps(
            {
                "tool_input": {
                    "step_name": "investigate",
                    "order_id": "",  # empty, falls back to env
                }
            }
        )

        code, stdout = _run(event, env={"AUTOSKILLIT_DISPATCH_ID": "pipeline-x"}, cwd=tmp_path)
        assert code == 0
        decision = json.loads(stdout)
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
