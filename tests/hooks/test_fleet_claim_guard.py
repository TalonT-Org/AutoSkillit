"""Tests for fleet_claim_guard PreToolUse hook.

The guard blocks fresh dispatch_food_truck calls when the target issue already
has an `in-progress` label, forcing the orchestrator to resume the prior
session instead. Tests cover allow/deny paths, fail-open behaviors on
malformed input or `gh` subprocess errors, and the deny-trigger constant.
"""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from unittest import mock

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_ISSUE_URL = "https://github.com/example/repo/issues/42"


def _run_guard(stdin_data: str | dict) -> str:
    from autoskillit.hooks.guards.fleet_claim_guard import main

    if isinstance(stdin_data, dict):
        stdin_text = json.dumps(stdin_data)
    else:
        stdin_text = stdin_data
    buf = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(stdin_text)):
        try:
            with redirect_stdout(buf):
                main()
        except SystemExit:
            pass
    return buf.getvalue()


def _mock_gh(labels_by_url: dict[str, list[dict[str, str]]]):
    """Return a mock subprocess.run keyed by full issue URL.

    The guard calls: ["gh", "issue", "view", NUMBER, "--repo", REPO, "--json", "labels"]
    """

    def _mock_run(cmd, *, capture_output=True, text=True, timeout=5):
        number = cmd[3]
        repo = cmd[5]
        url_key = f"https://github.com/{repo}/issues/{number}"
        labels = labels_by_url.get(url_key, [])
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"labels": labels}), stderr=""
        )

    return _mock_run


# ---------------------------------------------------------------------------
# Allow paths
# ---------------------------------------------------------------------------


def test_fresh_dispatch_on_unclaimed_issue_allows() -> None:
    """T1: Fresh dispatch with no in-progress label — allow."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {
            "ingredients": {"issue_urls": _ISSUE_URL},
        },
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        _mock_gh({_ISSUE_URL: []}),
    ):
        output = _run_guard(event)
    assert output == "", f"Expected allow but guard produced: {output!r}"


def test_resume_dispatch_on_claimed_issue_allows() -> None:
    """T3: Resume session id present — allow (resume is legitimate)."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {
            "resume_session_id": "sess-123",
            "ingredients": {"issue_urls": _ISSUE_URL},
        },
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        _mock_gh({_ISSUE_URL: [{"name": "in-progress"}]}),
    ):
        output = _run_guard(event)
    assert output == "", f"Expected allow but guard produced: {output!r}"


def test_no_issue_urls_allows() -> None:
    """T4: Missing issue_urls — allow (no URL to check, fail-open)."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {}},
    }
    output = _run_guard(event)
    assert output == ""


def test_missing_ingredients_key_allows() -> None:
    """T5: No ingredients key at all — allow (fail-open)."""
    event = {"tool_name": "dispatch_food_truck", "tool_input": {}}
    output = _run_guard(event)
    assert output == ""


def test_gh_subprocess_timeout_allows() -> None:
    """T6: gh subprocess timeout — allow (fail-open)."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {"issue_urls": _ISSUE_URL}},
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
    ):
        output = _run_guard(event)
    assert output == ""


def test_gh_subprocess_nonzero_exit_allows() -> None:
    """T7: gh subprocess non-zero exit — allow (fail-open)."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {"issue_urls": _ISSUE_URL}},
    }
    failure = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="error")
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        return_value=failure,
    ):
        output = _run_guard(event)
    assert output == ""


def test_malformed_stdin_allows() -> None:
    """T8: Malformed stdin — allow (fail-open)."""
    output = _run_guard("not-json")
    assert output == ""


# ---------------------------------------------------------------------------
# Deny paths
# ---------------------------------------------------------------------------


def test_fresh_dispatch_on_claimed_issue_denies() -> None:
    """T2: Fresh dispatch on a claimed issue — deny."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {"issue_urls": _ISSUE_URL}},
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        _mock_gh({_ISSUE_URL: [{"name": "in-progress"}]}),
    ):
        output = _run_guard(event)
    assert output, "Guard produced no output — bypass was not caught"
    parsed = json.loads(output)
    decision = parsed["hookSpecificOutput"]["permissionDecision"]
    reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
    assert decision == "deny"
    assert "in-progress" in reason
    assert "resume" in reason


def test_multi_issue_dispatch_any_claimed_denies() -> None:
    """T9: Multi-issue dispatch where ANY has in-progress — deny."""
    url1 = "https://github.com/example/repo/issues/1"
    url2 = "https://github.com/example/repo/issues/2"
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {"issue_urls": f"{url1},{url2}"}},
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        _mock_gh({url1: [], url2: [{"name": "in-progress"}]}),
    ):
        output = _run_guard(event)
    assert output
    parsed = json.loads(output)
    reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert url2 in reason


def test_deny_message_is_actionable() -> None:
    """T11: Deny message tells the LLM what fields to use."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {"issue_urls": _ISSUE_URL}},
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        _mock_gh({_ISSUE_URL: [{"name": "in-progress"}]}),
    ):
        output = _run_guard(event)
    assert output
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "resume_session_id" in reason
    assert "prior_dispatch_id" in reason


# ---------------------------------------------------------------------------
# Constant and registration
# ---------------------------------------------------------------------------


def test_fleet_claim_deny_trigger_constant_exists() -> None:
    """T12: FLEET_CLAIM_DENY_TRIGGER constant is a non-empty string."""
    from autoskillit.hooks.guards import fleet_claim_guard

    trigger = fleet_claim_guard.FLEET_CLAIM_DENY_TRIGGER
    assert isinstance(trigger, str)
    assert trigger


def test_fleet_claim_deny_trigger_appears_in_deny_message() -> None:
    """T13: The trigger string appears in the deny message."""
    event = {
        "tool_name": "dispatch_food_truck",
        "tool_input": {"ingredients": {"issue_urls": _ISSUE_URL}},
    }
    with mock.patch(
        "autoskillit.hooks.guards.fleet_claim_guard.subprocess.run",
        _mock_gh({_ISSUE_URL: [{"name": "in-progress"}]}),
    ):
        output = _run_guard(event)
    from autoskillit.hooks.guards import fleet_claim_guard

    assert output
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    assert fleet_claim_guard.FLEET_CLAIM_DENY_TRIGGER in reason


def test_fleet_claim_guard_registered() -> None:
    """T10: Guard is registered in HOOK_REGISTRY under dispatch_food_truck."""
    from autoskillit.hook_registry import HOOK_REGISTRY, NEW_SUBDIR_BASENAMES

    found_in_scripts = False
    for hook_def in HOOK_REGISTRY:
        if "dispatch_food_truck" in (hook_def.matcher or ""):
            if "guards/fleet_claim_guard.py" in hook_def.scripts:
                found_in_scripts = True
                break
    assert found_in_scripts, "guards/fleet_claim_guard.py not in dispatch_food_truck scripts"

    assert "fleet_claim_guard.py" in NEW_SUBDIR_BASENAMES
