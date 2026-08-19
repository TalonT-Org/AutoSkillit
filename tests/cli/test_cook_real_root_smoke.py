"""Opt-in live gate: cli.cook() against the real project root's real
.claude/settings.local.json (#4684 Fix B, Step 1.13/2.10).

Gated by AUTOSKILLIT_COOK_REAL_ROOT_SMOKE=1 — this is the live gate for the
cook-loop regression, patterned after
tests/server/test_claude_explorer_live_gate.py. Every other cook CLI test
uses tmp_path fixtures (arrange_cook creates an empty tmp_path/"project");
this is the one test that drives the real prepare-launch path against this
repository's own project root and its actual .claude/settings.local.json
file, in place, with an explicit backup/restore.

Only the subprocess spawn/wait step is mocked (via the same
run_cook_attempt capture arrange_cook uses for every other cook test) —
resolve_project_dir, config loading, and the real .claude/settings.local.json
read are unmocked. prepare_interactive_launch's executable-probe half still
needs a real `claude` binary on PATH, hence the "claude installed"
precondition (mirrors test_claude_explorer_live_gate.py's preconditions).

The test writes a KNOWN-GOOD settings file over the real one before running,
and restores the original content afterward. If restoration fails, the test
fails loudly rather than leaving the repository's own settings.local.json in
a test-modified state silently.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.config import AutomationConfig
from autoskillit.core import CmdSpec
from autoskillit.execution.backends import ClaudeCodeBackend
from tests.cli._cook_launch_helpers import arrange_cook

pytestmark = [pytest.mark.layer("cli"), pytest.mark.large, pytest.mark.smoke]

_ROOT = Path(__file__).resolve().parents[2]
_LIVE_ENV = "AUTOSKILLIT_COOK_REAL_ROOT_SMOKE"
_SETTINGS_PATH = _ROOT / ".claude" / "settings.local.json"
_SOURCE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
_has_authentication = bool(
    os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    or _SOURCE_CREDENTIALS.is_file()
)
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1" or shutil.which("claude") is None or not _has_authentication,
    reason="Cook real-root smoke gate requires its opt-in, executable, and isolated auth",
)


@contextmanager
def _settings_local_json_round_trip(content: dict) -> Iterator[None]:
    """Back up the real .claude/settings.local.json, write `content` in its
    place, yield, then restore the original — FAILS LOUDLY (does not
    silently swallow) if restoration fails."""
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    had_original = _SETTINGS_PATH.is_file()
    original = _SETTINGS_PATH.read_bytes() if had_original else None
    _SETTINGS_PATH.write_text(json.dumps(content))
    try:
        yield
    finally:
        try:
            if had_original:
                assert original is not None
                _SETTINGS_PATH.write_bytes(original)
            else:
                _SETTINGS_PATH.unlink()
        except OSError as exc:
            pytest.fail(
                f"FAILED TO RESTORE {_SETTINGS_PATH} after the live gate — "
                f"the repository's real settings.local.json may be left in a "
                f"test-modified state: {exc}",
                pytrace=False,
            )


@_skip_unless_live_gate
def test_cook_real_root_settings_local_json_composition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Live gate for the cook-loop regression (#4684), driven against the
    real project root and its real .claude/settings.local.json, backed up
    and restored around each sub-case:

    1. cli.cook() returns without ValueError when settings.local.json exists
       and force_inactive_agent_teams is False.
    2. with CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 in the real
       settings.local.json and force_inactive_agent_teams=True, cli.cook()
       strips the entry from the real file in place and launches with an
       agent-teams-free env — the composition PR #4613 broke and this plan's
       opt-in fixes. Per #4688 the opt-in remediates rather than refuses, so
       the assertion is on the rewrite, not on a raise.

    A single test function (not two) so the Taskfile's post-validation of
    "exactly one non-skipped test" mirrors test_claude_explorer_live_gate.py.
    """
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project: None,
    )

    no_error_config = AutomationConfig()
    no_error_config.agent_backend.force_inactive_agent_teams = False
    with _settings_local_json_round_trip({}):
        captured_ok: list[CmdSpec] = arrange_cook(
            monkeypatch,
            tmp_path / "no_error_case",
            config=no_error_config,
            project_dir_override=_ROOT,
        )
        cli.cook(backend=ClaudeCodeBackend())
    assert len(captured_ok) == 1

    strip_config = AutomationConfig()
    strip_config.agent_backend.force_inactive_agent_teams = True
    with _settings_local_json_round_trip({"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}}):
        captured_stripped: list[CmdSpec] = arrange_cook(
            monkeypatch,
            tmp_path / "strip_case",
            config=strip_config,
            project_dir_override=_ROOT,
        )
        cli.cook(backend=ClaudeCodeBackend())
        # Asserted inside the round-trip: the context manager restores the
        # original file on exit, so the rewrite is only observable here.
        rewritten = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in rewritten.get("env", {})
    assert len(captured_stripped) == 1
    assert captured_stripped[0].force_inactive_agent_teams is True
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in captured_stripped[0].env
