"""Session-replay harness: drives realistic multi-command sessions through the
full PreToolUse hook chain via the real dispatcher, asserting no benign
command is blocked and no failure-grade message appears without errors.

Remediation item 5c. Each fixture event is dispatched to every PreToolUse
guard whose HOOK_REGISTRY matcher matches the tool name and whose
session_scope is compatible with the fixture's simulated session (mirroring
which guards would actually be wired for that session type, since
session_scope gating happens at hooks.json generation time, not inside each
guard) — via the real ``hooks/_dispatch.py`` subprocess, exactly as Claude
Code would invoke it.

One narrow exception to the PreToolUse-only scope: ``dispatch_capture_lifecycle_hook``
additionally dispatches the SessionStart ``capture_lifecycle_hook.py`` once
per replay, the only consumer of the W2/W3 diagnostic-severity and
convergence machinery (``reconcile_capture_store`` / ``classify_cleanup_outcome``)
— unreachable through the per-event PreToolUse loop above.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import pkg_root
from autoskillit.hook_registry import HOOK_REGISTRY, HookDef
from autoskillit.hooks._capture._snapshot import (
    CaptureMeasurement,
    CommandOutcome,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture_artifacts import (
    create_capture_artifact,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks._capture_lifecycle import CaptureLifecycleStore

from .conftest import _FAILURE_GRADE_RE
from .fixtures.session_replays import INCIDENT_TRANSCRIPT, fixture_path

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_DISPATCH_PATH = pkg_root() / "hooks" / "_dispatch.py"
_TIMEOUT_SECONDS = 15


class ReplayEvent:
    __slots__ = ("payload", "allowed", "max_severity")

    def __init__(self, payload: dict[str, Any], allowed: bool, max_severity: str) -> None:
        self.payload = payload
        self.allowed = allowed
        self.max_severity = max_severity


def _substitute(obj: Any, mapping: dict[str, str]) -> Any:
    """Recursively replace {{TOKEN}} placeholders in every string value."""
    if isinstance(obj, str):
        for token, value in mapping.items():
            obj = obj.replace(token, value)
        return obj
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, mapping) for v in obj]
    return obj


def load_replay_fixture(
    path: Path, mapping: dict[str, str]
) -> tuple[dict[str, str], list[dict[str, Any]], list[ReplayEvent]]:
    """Parse a session-replay JSONL fixture.

    Returns (session_env, state_setup, events) with all {{TOKEN}} placeholders
    substituted per ``mapping``.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"empty fixture: {path}")
    header = _substitute(json.loads(lines[0]), mapping)
    session_env: dict[str, str] = header.get("session_env", {})
    state_setup: list[dict[str, Any]] = header.get("state_setup", [])

    events: list[ReplayEvent] = []
    for line in lines[1:]:
        raw = _substitute(json.loads(line), mapping)
        expectations = raw.get("expectations", {})
        events.append(
            ReplayEvent(
                payload=raw["payload"],
                allowed=bool(expectations.get("allowed", True)),
                max_severity=str(expectations.get("max_severity", "none")),
            )
        )
    return session_env, state_setup, events


def apply_state_setup(root: Path, state_setup: list[dict[str, Any]]) -> None:
    """Write each state_setup entry's file under ``root``."""
    for entry in state_setup:
        target = root / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry["content"], encoding="utf-8")


def _hook_scope_compatible(hook_def: HookDef, session_env: dict[str, str]) -> bool:
    """Mirror hook_registry.hook_applies_to_backend's session_scope gate.

    session_scope filtering happens at hooks.json *generation* time (which
    hooks get wired for a given session type) — a guard scoped headless_only
    is never dispatched at all for an interactive session, and vice versa.
    _dispatch.py has no notion of this, so the harness must apply the same
    filter a real deployed session would have applied before this event ever
    reached a guard.
    """
    if hook_def.session_scope == "any":
        return True
    headless = session_env.get("AUTOSKILLIT_HEADLESS") == "1"
    if hook_def.session_scope == "headless_only":
        return headless
    if hook_def.session_scope == "interactive_only":
        return not headless
    return True


def matched_hook_defs(tool_name: str, session_env: dict[str, str]) -> list[HookDef]:
    """Return every PreToolUse HookDef whose matcher fullmatches tool_name
    and whose session_scope is compatible with session_env."""
    return [
        hook_def
        for hook_def in HOOK_REGISTRY
        if hook_def.event_type == "PreToolUse"
        and re.fullmatch(hook_def.matcher, tool_name)
        and _hook_scope_compatible(hook_def, session_env)
    ]


class GuardRunResult:
    __slots__ = ("script", "stdout", "stderr", "denied")

    def __init__(self, script: str, stdout: str, stderr: str) -> None:
        self.script = script
        self.stdout = stdout
        self.stderr = stderr
        self.denied = _is_denied(stdout)


def _is_denied(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def run_event_through_matched_guards(
    event: ReplayEvent,
    session_env: dict[str, str],
    process_cwd: Path,
) -> list[GuardRunResult]:
    """Dispatch one event to every matched guard via the real _dispatch.py.

    Runs sequentially per matched script — matches the real Claude Code host,
    which invokes each PreToolUse hook command for a matcher group in order.
    """
    tool_name = event.payload.get("tool_name", "")
    results: list[GuardRunResult] = []
    child_env = {**os.environ, **session_env}
    stdin_bytes = json.dumps(event.payload).encode("utf-8")

    for hook_def in matched_hook_defs(tool_name, session_env):
        for script in hook_def.scripts:
            logical_name = script.removesuffix(".py")
            proc = subprocess.run(
                [sys.executable, str(_DISPATCH_PATH), logical_name],
                input=stdin_bytes,
                capture_output=True,
                cwd=str(process_cwd),
                env=child_env,
                timeout=_TIMEOUT_SECONDS,
            )
            assert proc.returncode == 0, (
                f"guard subprocess {script} exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            results.append(
                GuardRunResult(
                    script=script,
                    stdout=proc.stdout.decode("utf-8", errors="replace"),
                    stderr=proc.stderr.decode("utf-8", errors="replace"),
                )
            )
    return results


def replay(
    fixture_name: str,
    mapping: dict[str, str],
    process_cwd: Path,
) -> list[tuple[ReplayEvent, list[GuardRunResult]]]:
    """Load and fully execute a fixture. Returns (event, guard_results) pairs."""
    session_env, state_setup, events = load_replay_fixture(fixture_path(fixture_name), mapping)
    apply_state_setup(process_cwd, state_setup)
    return [
        (event, run_event_through_matched_guards(event, session_env, process_cwd))
        for event in events
    ]


def assert_replay_clean(replayed: list[tuple[ReplayEvent, list[GuardRunResult]]]) -> None:
    """The two standing invariants: no benign event is denied by any matched
    guard, and no stderr line carries failure-grade wording unless the
    event's max_severity explicitly permits it."""
    allow_violations: list[str] = []
    severity_violations: list[str] = []

    for event, results in replayed:
        tool_name = event.payload.get("tool_name", "")
        cmd = event.payload.get("tool_input", {}).get("cmd") or event.payload.get(
            "tool_input", {}
        ).get("command", "")

        if event.allowed:
            denying = [r.script for r in results if r.denied]
            if denying:
                allow_violations.append(
                    f"{tool_name} {cmd!r} was denied by: {denying} (expected allow)"
                )

        for r in results:
            for line in r.stderr.splitlines():
                if _FAILURE_GRADE_RE.search(line) and event.max_severity != "failure":
                    severity_violations.append(
                        f"{r.script} on {tool_name} {cmd!r} "
                        f"(max_severity={event.max_severity!r}): {line!r}"
                    )

    assert not allow_violations, "Benign events denied by a matched guard:\n" + "\n".join(
        allow_violations
    )
    assert not severity_violations, (
        "Failure-grade stderr wording without a failure-severity event:\n"
        + "\n".join(severity_violations)
    )


# ---------------------------------------------------------------------------
# Capture-lifecycle backlog seeding (W2/W3 convergence machinery)
# ---------------------------------------------------------------------------

_RETENTION_SECONDS = 3600.0
_BACKLOG_BACKDATE_SECONDS = _RETENTION_SECONDS + 100.0


def seed_capture_backlog(project_root: Path, *, count: int) -> None:
    """Pre-seed the capture-lifecycle store under ``project_root`` with
    ``count`` genuinely eligible (past-retention) records via the real
    production store API — mirrors ``tests/hooks/test_capture_lifecycle.py``'s
    ``_open_store``/``_seed_finalized_captures`` pattern.

    The generic JSONL ``state_setup`` mechanism (``apply_state_setup``) can
    only write plain-text files; the capture-lifecycle store's ledger is a
    binary, SHA-256-framed format that cannot be hand-authored that way, so
    this seeds through the real store-construction API instead. The wall
    clock is backdated only at write time, so the records are already past
    their retention deadline by the time the real (unmocked) reconcile call
    inside ``dispatch_capture_lifecycle_hook`` runs against the real system
    clock moments later.
    """
    seed_wall = time.time() - _BACKLOG_BACKDATE_SECONDS

    def wall_clock() -> float:
        return seed_wall

    anchor = open_project_anchor(str(project_root))
    try:
        root = open_capture_root(anchor, create=True)
    except BaseException:
        anchor.close()
        raise
    try:
        store = CaptureLifecycleStore.from_open_authorities(
            anchor, root, wall_clock=wall_clock, monotonic=time.monotonic
        )
        for index in range(count):
            capture_id = f"{index + 1:016x}"
            artifact = create_capture_artifact(root, capture_id, store)
            data = b"seeded-backlog-record"
            os.write(artifact.fd, data)
            measurement = CaptureMeasurement.from_bytes(data, inline_bytes=max(1, len(data)))
            snapshot = verify_capture_snapshot(
                fd=artifact.fd,
                capture_id=artifact.authority.capture_id,
                incarnation=artifact.authority.incarnation,
                project_identity=store._project_identity,
                root_identity=store._root_identity,
                carrier_name=artifact.name,
                carrier_identity=(artifact.identity.device, artifact.identity.inode),
                measurement=measurement,
                command_outcome=CommandOutcome.exited(0),
                expected_revision=artifact.authority.expected_revision,
                finalized_at=wall_clock(),
                retention_deadline=wall_clock() + _RETENTION_SECONDS,
            )
            store.commit_verified_snapshot(snapshot, issue_reference=False)
            artifact.close_artifact_fd()
            artifact.release_lease()
    finally:
        root.close()
        anchor.close()


def dispatch_capture_lifecycle_hook(project_root: Path) -> GuardRunResult:
    """Dispatch the SessionStart ``capture_lifecycle_hook.py`` once via the
    real ``_dispatch.py``, exactly as a real session start would.

    This is the only consumer of the W2/W3 diagnostic-severity and
    convergence machinery (``reconcile_capture_store`` /
    ``classify_cleanup_outcome``); the per-event loop above only ever
    dispatches PreToolUse hooks, so this is a deliberate, narrow addition
    for this one SessionStart hook rather than a general event-type
    extension to the harness.
    """
    stdin_bytes = json.dumps({"cwd": str(project_root)}).encode("utf-8")
    proc = subprocess.run(
        [sys.executable, str(_DISPATCH_PATH), "capture_lifecycle_hook"],
        input=stdin_bytes,
        capture_output=True,
        cwd=str(project_root),
        env=os.environ,
        timeout=_TIMEOUT_SECONDS,
    )
    return GuardRunResult(
        script="capture_lifecycle_hook.py",
        stdout=proc.stdout.decode("utf-8", errors="replace"),
        stderr=proc.stderr.decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# Fixture-driven tests
# ---------------------------------------------------------------------------


def test_incident_transcript_replay_is_clean(tmp_path: Path) -> None:
    """The incident-modeled transcript — dual-cwd worktree run_cmd calls,
    Bash reads, a benign loop, and one genuine gh pr review deny — must
    replay with zero spurious denials and zero unwarranted failure-grade
    stderr end to end through the real PreToolUse hook chain."""
    orchestrating_root = tmp_path / "orchestrating-project"
    worktree_root = tmp_path / "worktrees" / "impl-something"
    orchestrating_root.mkdir(parents=True)
    worktree_root.mkdir(parents=True)

    mapping = {
        "{{ORCHESTRATING_ROOT}}": str(orchestrating_root),
        "{{WORKTREE_ROOT}}": str(worktree_root),
    }
    replayed = replay(INCIDENT_TRANSCRIPT, mapping, process_cwd=orchestrating_root)
    assert_replay_clean(replayed)


def test_incident_transcript_genuine_mutation_is_actually_denied(tmp_path: Path) -> None:
    """Companion to the clean-replay assertion: the one event expected to be
    denied really is — assert_replay_clean only checks allowed:true events,
    so this closes the loop on the fixture's one deny case."""
    orchestrating_root = tmp_path / "orchestrating-project"
    worktree_root = tmp_path / "worktrees" / "impl-something"
    orchestrating_root.mkdir(parents=True)
    worktree_root.mkdir(parents=True)

    mapping = {
        "{{ORCHESTRATING_ROOT}}": str(orchestrating_root),
        "{{WORKTREE_ROOT}}": str(worktree_root),
    }
    replayed = replay(INCIDENT_TRANSCRIPT, mapping, process_cwd=orchestrating_root)
    deny_events = [(e, r) for e, r in replayed if not e.allowed]
    assert deny_events, "fixture must contain at least one deny-expected event"
    for event, results in deny_events:
        assert any(r.denied for r in results), (
            f"expected event {event.payload!r} to be denied by some matched guard, "
            f"but none of {[r.script for r in results]} denied it"
        )


def test_incident_transcript_capture_wrapped_command_over_seeded_backlog_is_clean(
    tmp_path: Path,
) -> None:
    """W4 Step 1c: the transcript's capture-wrapped Bash event (routed
    through shell_capture_hook.py's rewrite-into-runner path) must stay
    clean even when the capture-lifecycle store already carries a
    genuinely eligible (past-retention) backlog -- exercising the W2/W3
    diagnostic-severity and convergence machinery end-to-end, not only the
    W1 guard-decision path the other replay tests cover.
    """
    orchestrating_root = tmp_path / "orchestrating-project"
    worktree_root = tmp_path / "worktrees" / "impl-something"
    orchestrating_root.mkdir(parents=True)
    worktree_root.mkdir(parents=True)

    seed_capture_backlog(orchestrating_root, count=3)

    mapping = {
        "{{ORCHESTRATING_ROOT}}": str(orchestrating_root),
        "{{WORKTREE_ROOT}}": str(worktree_root),
    }
    replayed = replay(INCIDENT_TRANSCRIPT, mapping, process_cwd=orchestrating_root)
    assert_replay_clean(replayed)

    lifecycle_result = dispatch_capture_lifecycle_hook(orchestrating_root)
    assert not lifecycle_result.denied
    for line in lifecycle_result.stderr.splitlines():
        assert not _FAILURE_GRADE_RE.search(line), (
            "capture_lifecycle_hook reconcile over a seeded backlog produced "
            f"failure-grade stderr: {line!r}"
        )
