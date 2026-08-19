"""End-to-end cook against a populated .claude/settings.local.json (#4684 Fix B).

Every other cook CLI test stubs validate_interactive_invocation to a no-op
(11+ locations across tests/cli/) or uses tmp_path fixtures that never
populate .claude/settings.local.json — so the real
_interactive_invocation_environment_policy check has never run against a
real cook() invocation. This test does not stub the validator; it exercises
the real policy against a real settings file, which is exactly the
composition PR #4613 broke (#4684).

The check is opt-in on the artifact (CmdSpec.force_inactive_agent_teams,
threaded from AutomationConfig.agent_backend.force_inactive_agent_teams).
Per #4688, the opt-in *remediates* rather than refuses: conflicting settings
entries are stripped before the inactivity confirmation, so a repository
whose settings enable teams is neutralized and launched rather than
rejected (refusing would turn away precisely the population this opt-in
serves). What must hold either way is that the opt-in is genuinely live —
with it on, a real settings file is actually rewritten and the launch env
carries no agent-teams var; with it off, the repository is left
byte-for-byte unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.config import AutomationConfig
from autoskillit.core import atomic_write
from autoskillit.execution.backends import ClaudeCodeBackend
from tests.cli._cook_launch_helpers import arrange_cook

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_AGENT_TEAMS_VAR = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"


def _write_claude_shim(path: Path) -> None:
    atomic_write(
        path,
        "#!/bin/sh\n"
        'if [ "${1-}" = "--version" ]; then\n'
        "  printf '%s\\n' '2.1.220 (Claude Code)'\n"
        "fi\n"
        "exit 0\n",
    )
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("settings_content", "force_inactive", "expect_key_stripped"),
    [
        pytest.param(
            {"env": {_AGENT_TEAMS_VAR: "1"}},
            False,
            False,
            id="opt_in_off_leaves_settings_untouched",
        ),
        pytest.param(
            {"env": {_AGENT_TEAMS_VAR: "1"}},
            True,
            True,
            id="strips_when_force_inactive_true_and_setting_active",
        ),
        pytest.param(
            {"env": {_AGENT_TEAMS_VAR: "true"}},
            True,
            True,
            id="strips_truthy_values_normalized",
        ),
        pytest.param(
            {},
            True,
            False,
            id="no_op_when_setting_absent",
        ),
        pytest.param(
            {"env": {_AGENT_TEAMS_VAR: "0"}},
            True,
            True,
            id="strips_even_falsy_value",
        ),
    ],
)
def test_cook_against_populated_settings_local_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings_content: dict,
    force_inactive: bool,
    expect_key_stripped: bool,
) -> None:
    shim = tmp_path / "claude"
    _write_claude_shim(shim)
    monkeypatch.setenv("PATH", str(tmp_path))
    # Hermetic regardless of the host/dev-session's own env — this repo's own
    # .claude/settings.local.json may set this var for the *outer* session.
    monkeypatch.delenv(_AGENT_TEAMS_VAR, raising=False)

    config = AutomationConfig()
    config.agent_backend.force_inactive_agent_teams = force_inactive
    # do NOT stub validate_interactive_invocation — the real policy must run.
    captured = arrange_cook(
        monkeypatch, tmp_path, config=config, settings_content=settings_content
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_reload.consume_reload_sentinel",
        lambda _project: None,
    )

    cli.cook(backend=ClaudeCodeBackend())

    assert len(captured) == 1
    spec = captured[0]
    # The caller's intent is stamped onto the artifact the checkpoint reads.
    assert spec.force_inactive_agent_teams is force_inactive
    if force_inactive:
        assert _AGENT_TEAMS_VAR not in spec.env

    settings_file = tmp_path / "project" / ".claude" / "settings.local.json"
    written = json.loads(settings_file.read_text(encoding="utf-8"))
    if expect_key_stripped:
        assert _AGENT_TEAMS_VAR not in written.get("env", {}), (
            "the opt-in must actually rewrite the repository settings file"
        )
    else:
        assert written == settings_content, (
            "settings must be left byte-for-byte unchanged when nothing needs stripping"
        )
