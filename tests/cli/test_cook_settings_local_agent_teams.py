"""End-to-end cook against a populated .claude/settings.local.json (#4684 Fix B).

Every other cook CLI test stubs validate_interactive_invocation to a no-op
(11+ locations across tests/cli/) or uses tmp_path fixtures that never
populate .claude/settings.local.json — so the real
_interactive_invocation_environment_policy check has never run against a
real cook() invocation. This test does not stub the validator; it exercises
the real policy against a real settings file, which is exactly the
composition PR #4613 broke (#4684).

The check is opt-in on the artifact (CmdSpec.force_inactive_agent_teams,
threaded from AutomationConfig.agent_backend.force_inactive_agent_teams):
it must be a no-op whenever the opt-in is off, and must positively confirm
an inactive setting when the opt-in is on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.config import AutomationConfig
from autoskillit.core import atomic_write
from autoskillit.execution.backends import ClaudeCodeBackend
from tests.cli._cook_launch_helpers import arrange_cook

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


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
    ("settings_content", "force_inactive", "expect_error"),
    [
        pytest.param(
            {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}},
            False,
            False,
            id="no_error_when_force_inactive_false",
        ),
        pytest.param(
            {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}},
            True,
            True,
            id="raises_when_force_inactive_true_and_setting_active",
        ),
        pytest.param(
            {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "true"}},
            True,
            True,
            id="raises_truthy_values_normalized",
        ),
        pytest.param(
            {},
            True,
            False,
            id="no_error_when_setting_absent",
        ),
        pytest.param(
            {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0"}},
            True,
            False,
            id="no_error_when_setting_falsy",
        ),
    ],
)
def test_cook_against_populated_settings_local_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings_content: dict,
    force_inactive: bool,
    expect_error: bool,
) -> None:
    shim = tmp_path / "claude"
    _write_claude_shim(shim)
    monkeypatch.setenv("PATH", str(tmp_path))
    # Hermetic regardless of the host/dev-session's own env — this repo's own
    # .claude/settings.local.json may set this var for the *outer* session.
    monkeypatch.delenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", raising=False)

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

    if expect_error:
        with pytest.raises(RuntimeError, match="force_inactive_agent_teams requested"):
            cli.cook(backend=ClaudeCodeBackend())
        assert captured == []
    else:
        cli.cook(backend=ClaudeCodeBackend())
        assert len(captured) == 1
