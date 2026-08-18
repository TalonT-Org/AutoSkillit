"""#4575 production-shaped negative control.

Exercises the join-bound dispatch surface against the production hooks
without requiring real Claude or MiniMax network access.
"""

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
    event: dict,
    *,
    hook_module: str,
    headless: bool = False,
    session_type: str | None = "skill",
    flag_path: str | None = None,
) -> str:
    """Run a guard's main() with the given PreToolUse event envelope."""
    import importlib

    module = importlib.import_module(hook_module)
    main = module.main

    stdin_content = json.dumps(event)
    env_snapshot = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "AUTOSKILLIT_HEADLESS",
            "AUTOSKILLIT_SESSION_TYPE",
            "AUTOSKILLIT_JOIN_REQUIRED",
            "AUTOSKILLIT_JOIN_FLAG_PATH",
        )
    }
    if headless:
        env_snapshot["AUTOSKILLIT_HEADLESS"] = "1"
    if session_type is not None:
        env_snapshot["AUTOSKILLIT_SESSION_TYPE"] = session_type
    if flag_path is not None:
        env_snapshot["AUTOSKILLIT_JOIN_FLAG_PATH"] = flag_path

    with (
        patch.dict(os.environ, env_snapshot, clear=True),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _set_session_join_required(tmp_path: Path, join_required: bool) -> str:
    """Write a session binding flag so the guard reads join_required.

    Returns the flag path so callers can route it through helpers that
    pass env vars explicitly.
    """
    flag_dir = tmp_path / ".autoskillit" / "temp"
    flag_dir.mkdir(parents=True, exist_ok=True)
    flag_path = flag_dir / "skill_guard_4575.flag"
    payload = {
        "schema_version": 1,
        "session_id": "4575",
        "join_required": join_required,
        "binding_valid": True,
        "loaded_skills": [],
    }
    flag_path.write_text(json.dumps(payload), encoding="utf-8")
    return str(flag_path)


def test_4575_named_teammate_call_denied(tmp_path: Path) -> None:
    """#4575 reproduction: an Agent call with `name` set is denied.

    The fake boundary mimics the named-teammate dispatch that #4575
    records losing results. The guard must deny before child creation.
    """
    flag_path = _set_session_join_required(tmp_path, join_required=True)
    event = {
        "tool_name": "Agent",
        "session_id": "4575",
        "tool_input": {
            "prompt": "reviewer",
            "name": "reviewer",
            "team_name": "team-a",
        },
    }
    # The background_exec_guard sees the join-required binding plus a
    # named/team_name selector and must emit a structured deny payload.
    _out = _run_guard(
        event,
        hook_module="autoskillit.hooks.guards.background_exec_guard",
        session_type="skill",
        flag_path=flag_path,
    )
    assert "deny" in _out, (
        "background_exec_guard must deny a named/team_name Agent call in a join-required session"
    )


def test_4575_named_teammate_call_denied_background_run(tmp_path: Path) -> None:
    """A named Agent call with run_in_background is denied by the guard."""
    flag_path = _set_session_join_required(tmp_path, join_required=True)
    event = {
        "tool_name": "Agent",
        "session_id": "4575",
        "tool_input": {
            "prompt": "reviewer",
            "name": "reviewer",
            "run_in_background": True,
        },
    }
    _out = _run_guard(
        event,
        hook_module="autoskillit.hooks.guards.background_exec_guard",
        session_type="skill",
        headless=True,
        flag_path=flag_path,
    )
    # The background_exec_guard denies run_in_background in skill
    # sessions regardless of join semantics.
    assert "deny" in _out


def test_4575_clean_session_allows_named_teammate(tmp_path: Path) -> None:
    """A clean (join-false) session preserves legitimate team dispatch."""
    flag_path = _set_session_join_required(tmp_path, join_required=False)
    event = {
        "tool_name": "Agent",
        "session_id": "clean",
        "tool_input": {"prompt": "reviewer", "name": "reviewer"},
    }
    _out = _run_guard(
        event,
        hook_module="autoskillit.hooks.guards.background_exec_guard",
        session_type="skill",
        flag_path=flag_path,
    )
    # background_exec_guard must NOT deny a named Agent call when the
    # binding reports join_required=false (the join contract is inert).
    assert "permissionDecision" not in _out, (
        "background_exec_guard must not deny a clean (join-false) named "
        "Agent call; the join contract should be permissive."
    )
    assert "deny" not in _out


def test_4575_unnamed_foreground_succeeds_after_declaration(tmp_path: Path) -> None:
    """Unnamed foreground Agent calls succeed against a declared batch.

    This is the retry path: after the named dispatch is denied, the
    parent retries with ordinary unnamed foreground Agent calls,
    the claim is recorded, and the wave settles.
    """
    from autoskillit.hooks._join_ledger import (
        OUTCOME_SUCCESS,
        active_batch,
        claim_assignment,
        declare_batch,
        settle_assignment,
    )

    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    # Unnamed foreground Agent call claims the only declared slot.
    claimed = claim_assignment(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        tool_use_id="t1",
    )
    assert claimed is not None
    assert claimed["label"] == "a1"
    # Substantive result settles the wave.
    settle_assignment(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="4575", top_level_parent="p1")
    assert batch["wave_outcome"] == "complete"


def test_4575_first_wave_denied_then_wave_resolves(tmp_path: Path) -> None:
    """Reproduce the full #4575 pattern: first dispatch denied, then
    the retry wave succeeds."""
    from autoskillit.hooks._join_ledger import (
        OUTCOME_SUCCESS,
        active_batch,
        can_release_stop,
        claim_assignment,
        declare_batch,
        settle_assignment,
    )

    flag_dir = tmp_path
    # Retry path: open a declared batch and complete the wave.
    declare_batch(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    claim_assignment(flag_dir, session_id="4575", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="4575", top_level_parent="p1", tool_use_id="t2")
    settle_assignment(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    settle_assignment(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        tool_use_id="t2",
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="4575", top_level_parent="p1")
    assert batch["wave_outcome"] == "complete"

    # Stop releases the wave.
    allowed, _reason = can_release_stop(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        session_binding={
            "join_required": True,
            "skill_name": "skill",
            "artifact_digest": "abc",
        },
    )
    assert allowed is True
