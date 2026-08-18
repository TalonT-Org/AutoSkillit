"""prepare_interactive_launch must stamp the spec it actually launches.

The probe build carried ``force_inactive_agent_teams``; the final build — the one
that produces the launched spec — dropped it. Restoring it naively was not possible
either: the final build carries an executable binding, which the builder used to
refuse outright. Both halves had to be repaired together, so both are asserted here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.cli.session._session_launch import prepare_interactive_launch
from autoskillit.core.types._type_resume import NoResume
from autoskillit.execution.backends import ClaudeCodeBackend
from tests._realistic_project import (
    AGENT_TEAMS_ENV_VAR,
    make_realistic_project,
    write_fake_agent_binary,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _prepared_launch(project: Path, tmp_path: Path, *, force: bool):
    return prepare_interactive_launch(
        ClaudeCodeBackend(),
        project_dir=project,
        extra_env={"PATH": str(tmp_path / "bin")},
        required_env=None,
        plugin_binding=None,
        resume_spec=NoResume(),
        system_prompt=None,
        initial_prompt="hello",
        force_inactive_agent_teams=force,
    )


def test_prepare_interactive_launch_stamps_the_final_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe carried the flag; the build that produces the launched spec did not."""
    project = make_realistic_project(tmp_path, agent_teams=None)
    write_fake_agent_binary(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    prepared = _prepared_launch(project, tmp_path, force=True)

    assert prepared.spec.force_inactive_agent_teams is True
    assert AGENT_TEAMS_ENV_VAR not in prepared.spec.env
    assert prepared.executable is not None


def test_prepare_interactive_launch_survives_exported_teams_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case both naive repairs fail.

    With the variable exported, omitting the kwarg on the final build leaves
    the recomputed env un-neutralized and the binding equality check fails;
    passing it used to hit the executable-binding refusal instead.
    """
    project = make_realistic_project(tmp_path, agent_teams=None)
    write_fake_agent_binary(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv(AGENT_TEAMS_ENV_VAR, "1")

    prepared = _prepared_launch(project, tmp_path, force=True)

    assert prepared.spec.force_inactive_agent_teams is True
    assert AGENT_TEAMS_ENV_VAR not in prepared.spec.env
    assert AGENT_TEAMS_ENV_VAR not in prepared.executable.launch_environment


def test_prepare_interactive_launch_default_leaves_env_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default config must be byte-for-byte unaffected by the policy machinery."""
    project = make_realistic_project(tmp_path, agent_teams=None)
    write_fake_agent_binary(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv(AGENT_TEAMS_ENV_VAR, "1")

    prepared = _prepared_launch(project, tmp_path, force=False)

    assert prepared.spec.force_inactive_agent_teams is False
    assert prepared.spec.env[AGENT_TEAMS_ENV_VAR] == "1"
    assert os.environ[AGENT_TEAMS_ENV_VAR] == "1"
