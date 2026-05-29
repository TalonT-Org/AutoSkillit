"""Tests for recipe/_cmd_rpc_merge.py — base branch fetch discipline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _mock_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _fake_run_git_factory(
    responses: list[MagicMock] | None = None,
    call_log: list[list[str]] | None = None,
) -> object:
    """Return a side_effect callable that records commands and pops responses."""
    _responses = list(responses) if responses else []
    _call_log = call_log if call_log is not None else []

    def fake(cmd, *, cwd=None, check=False):  # noqa: ARG001
        _call_log.append(list(cmd))
        if _responses:
            result = _responses.pop(0)
        else:
            result = _mock_result(0)
        if check and result.returncode != 0:
            import subprocess

            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result

    return fake


def test_attempt_cheap_rebase_fetches_base_branch():
    """attempt_cheap_rebase must fetch base_branch before rebasing onto it."""
    from autoskillit.recipe._cmd_rpc_merge import attempt_cheap_rebase

    call_log: list[list[str]] = []
    fake = _fake_run_git_factory(call_log=call_log)

    with patch("autoskillit.recipe._cmd_rpc_merge.run_git", side_effect=fake):
        result = attempt_cheap_rebase("/work", "pr-branch", "develop")

    assert result == {"status": "clean"}
    fetch_base_indices = [
        i
        for i, cmd in enumerate(call_log)
        if cmd[:1] == ["fetch"] and "develop" in cmd and "pr-branch" not in cmd
    ]
    rebase_indices = [
        i for i, cmd in enumerate(call_log) if cmd[:1] == ["rebase"] and "--abort" not in cmd
    ]
    assert fetch_base_indices, f"fetch base_branch not found in call log: {call_log}"
    assert rebase_indices, f"rebase not found in call log: {call_log}"
    assert fetch_base_indices[0] < rebase_indices[0], (
        f"fetch base_branch (idx {fetch_base_indices[0]}) must precede "
        f"rebase (idx {rebase_indices[0]}); call_log={call_log}"
    )


def test_proactive_rebase_next_pr_fetches_base_branch():
    """proactive_rebase_next_pr must fetch base_branch before rebasing onto it."""
    from autoskillit.recipe._cmd_rpc_merge import proactive_rebase_next_pr

    call_log: list[list[str]] = []
    fake = _fake_run_git_factory(call_log=call_log)

    with patch("autoskillit.recipe._cmd_rpc_merge.run_git", side_effect=fake):
        result = proactive_rebase_next_pr("/work", "next-pr-branch", "develop")

    assert result == {"status": "clean"}
    fetch_base_indices = [
        i
        for i, cmd in enumerate(call_log)
        if cmd[:1] == ["fetch"] and "develop" in cmd and "next-pr-branch" not in cmd
    ]
    rebase_indices = [
        i for i, cmd in enumerate(call_log) if cmd[:1] == ["rebase"] and "--abort" not in cmd
    ]
    assert fetch_base_indices, f"fetch base_branch not found in call log: {call_log}"
    assert rebase_indices, f"rebase not found in call log: {call_log}"
    assert fetch_base_indices[0] < rebase_indices[0], (
        f"fetch base_branch (idx {fetch_base_indices[0]}) must precede "
        f"rebase (idx {rebase_indices[0]}); call_log={call_log}"
    )


def test_queue_ejected_fix_returns_fetch_error_on_network_failure():
    """queue_ejected_fix must return fetch_error (not conflicts) when fetch fails."""
    from autoskillit.recipe._cmd_rpc_merge import queue_ejected_fix

    responses = [
        _mock_result(1, "", ""),  # _detect_remote: upstream not found → origin
        _mock_result(1, "", "Could not resolve host: github.com"),  # fetch fails
    ]
    fake = _fake_run_git_factory(responses=responses)

    with patch("autoskillit.recipe._cmd_rpc_merge.run_git", side_effect=fake):
        result = queue_ejected_fix("/work", "develop")

    assert result["status"] == "fetch_error", f"Expected fetch_error, got: {result}"
    assert "stderr" in result
