"""The interactive checkpoint reads declared intent; it never infers policy.

A checkpoint that cannot see what the builder was asked to do degrades to
"always enforce", which turns every developer's own agent-teams setting into a
launch failure. Intent lives on the spec, so these are the four cases that
matter: default intent never enforces, declared intent always does, and the
builder confirms eagerly at construction either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import CmdSpec
from autoskillit.execution.backends import ClaudeCodeBackend
from tests._realistic_project import AGENT_TEAMS_ENV_VAR, make_realistic_project

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_default_intent_ignores_teams_enabled_project(tmp_path: Path) -> None:
    """The shipped defect: this raised on every legitimate teams-enabled repo."""
    project = make_realistic_project(tmp_path, agent_teams="1")
    backend = ClaudeCodeBackend()

    spec = backend.build_interactive_cmd(initial_prompt="hello")
    spec = CmdSpec(
        cmd=spec.cmd,
        env=spec.env,
        cwd=str(project),
        origin=spec.origin,
    )

    assert backend.validate_interactive_invocation(spec) == []


def test_declared_intent_reports_policy_violations(tmp_path: Path) -> None:
    """Fail-closed confirmation is preserved for the opt-in case."""
    project = make_realistic_project(tmp_path, agent_teams="1")
    backend = ClaudeCodeBackend()

    spec = CmdSpec(
        cmd=("claude", "--dangerously-skip-permissions"),
        env={AGENT_TEAMS_ENV_VAR: "1"},
        cwd=str(project),
        force_inactive_agent_teams=True,
    )

    errors = backend.validate_interactive_invocation(spec)

    assert errors
    assert any(AGENT_TEAMS_ENV_VAR in error for error in errors)


def test_declared_intent_passes_on_a_clean_project(tmp_path: Path) -> None:
    project = make_realistic_project(tmp_path, agent_teams=None)
    backend = ClaudeCodeBackend()

    spec = backend.build_interactive_cmd(
        initial_prompt="hello",
        force_inactive_agent_teams=True,
        project_root=str(project),
    )
    spec = CmdSpec(
        cmd=spec.cmd,
        env=spec.env,
        cwd=str(project),
        origin=spec.origin,
        force_inactive_agent_teams=spec.force_inactive_agent_teams,
    )

    assert spec.force_inactive_agent_teams is True
    assert backend.validate_interactive_invocation(spec) == []


def test_declared_intent_neutralizes_teams_enabled_settings(tmp_path: Path) -> None:
    """The opt-in must succeed on the repositories it exists to serve.

    Confirming before stripping refuses exactly the population that asked for
    neutralization, leaving the feature unreachable.
    """
    project = make_realistic_project(tmp_path, agent_teams="1")
    settings_local = project / ".claude" / "settings.local.json"
    backend = ClaudeCodeBackend()

    spec = backend.build_interactive_cmd(
        initial_prompt="hello",
        force_inactive_agent_teams=True,
        project_root=str(project),
    )

    assert spec.force_inactive_agent_teams is True
    assert AGENT_TEAMS_ENV_VAR not in spec.env
    assert AGENT_TEAMS_ENV_VAR not in settings_local.read_text()
    # Unrelated keys in the same file are left alone.
    assert "AUTOSKILLIT_UNRELATED" in settings_local.read_text()


def test_malformed_settings_are_scoped_to_declared_intent(tmp_path: Path) -> None:
    """Fail-closed on malformed settings, but only when neutralization is asked for.

    Ungated, this blocked launches over settings files unrelated to agent teams.
    """
    project = make_realistic_project(tmp_path, malformed_local=True)
    backend = ClaudeCodeBackend()

    default_spec = CmdSpec(cmd=("claude",), env={}, cwd=str(project))
    assert backend.validate_interactive_invocation(default_spec) == []

    forced_spec = CmdSpec(
        cmd=("claude",),
        env={},
        cwd=str(project),
        force_inactive_agent_teams=True,
    )
    errors = backend.validate_interactive_invocation(forced_spec)
    assert any("could not be parsed" in error for error in errors)


def test_builder_refuses_eagerly_when_neutralization_cannot_confirm(
    tmp_path: Path,
) -> None:
    """A malformed file cannot be rewritten, so construction must refuse."""
    project = make_realistic_project(tmp_path, malformed_local=True)
    backend = ClaudeCodeBackend()

    with pytest.raises(RuntimeError, match="could not be parsed"):
        backend.build_interactive_cmd(
            initial_prompt="hello",
            force_inactive_agent_teams=True,
            project_root=str(project),
        )
