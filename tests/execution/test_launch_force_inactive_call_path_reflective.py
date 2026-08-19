"""Reflective call-path test for force_inactive_agent_teams (#4684 Fix B / 2.7).

Replaces the hand-written 5-probe list in the retired
tests/execution/test_launch_force_inactive_call_path.py. That list silently
passed when a sixth ``build_*_cmd`` builder was added to ``ClaudeCodeBackend``
without the ``force_inactive_agent_teams`` parameter — nothing enumerated the
actual builder surface. This file discovers every ``build_*_cmd`` method via
``dir()`` and asserts each carries the opt-in parameter, mirroring the
reflective-enumeration pattern in
tests/arch/test_backend_protocol_completeness.py.

``build_inspector_cmd`` is excluded: it raises ``CapabilityNotSupportedError``/
``AssertionError`` and is not wired into any launch path (Plan § 2.2). ``build_cmd``
is excluded: it is the pipeline-internal skill-command dispatch entry point — it
delegates to ``build_headless_cmd()`` internally with the option fixed at its
default and forwards the resulting CmdSpec's field through, but does not expose
the parameter to its own caller. Both exclusions fall out of
``BUILDER_METHOD_NAME_REGEX`` without a hardcoded name list: neither name matches
``build_<something>_cmd`` with a non-empty, non-"inspector" middle segment.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import replace

import pytest

from autoskillit.core import CmdSpec
from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# Matches build_food_truck_cmd, build_headless_cmd, build_interactive_cmd,
# build_resume_cmd, build_skill_session_cmd. Deliberately excludes
# build_inspector_cmd (not launch-wired) and build_cmd (no middle segment —
# see module docstring).
BUILDER_METHOD_NAME_REGEX = re.compile(r"^build_(?!inspector).*_cmd$")


def _discover_builder_names() -> list[str]:
    backend = ClaudeCodeBackend()
    return sorted(
        name
        for name in dir(backend)
        if BUILDER_METHOD_NAME_REGEX.match(name) and callable(getattr(backend, name))
    )


def test_every_discovered_builder_accepts_force_inactive_agent_teams() -> None:
    """A new build_*_cmd builder must carry the opt-in parameter from day one."""
    backend = ClaudeCodeBackend()
    missing = [
        name
        for name in _discover_builder_names()
        if "force_inactive_agent_teams" not in inspect.signature(getattr(backend, name)).parameters
    ]
    assert not missing, (
        f"Builder(s) missing force_inactive_agent_teams parameter: {missing}. "
        "Every build_*_cmd builder must carry the opt-in per Plan § 2.2/2.7."
    )


def _headless_spec(force: bool) -> CmdSpec:
    backend = ClaudeCodeBackend()
    spec = backend.build_headless_cmd(
        "hello",
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
        project_root="/tmp",
    )
    return spec


def _skill_session_spec(force: bool) -> CmdSpec:
    backend = ClaudeCodeBackend()
    spec = backend.build_skill_session_cmd(
        "/test",
        provider_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
        project_root="/tmp",
    )
    return spec


def _food_truck_spec(force: bool) -> CmdSpec:
    backend = ClaudeCodeBackend()
    spec = backend.build_food_truck_cmd(
        orchestrator_prompt="orchestrate",
        plugin_binding=None,
        cwd="/tmp",
        completion_marker="DONE",
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
        project_root="/tmp",
    )
    return spec


def _resume_spec(force: bool) -> CmdSpec:
    backend = ClaudeCodeBackend()
    spec = backend.build_resume_cmd(
        resume_session_id="abc",
        prompt="resume",
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
        project_root="/tmp",
    )
    return spec


def _interactive_spec(force: bool) -> CmdSpec:
    backend = ClaudeCodeBackend()
    spec = backend.build_interactive_cmd(
        initial_prompt="hello",
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
        project_root="/tmp",
    )
    return spec


# Behavioral probes, keyed by builder name so completeness against the
# reflective discovery above can be verified — a new builder must register a
# probe here, not just gain the parameter, or test_every_discovered_builder_
# has_a_behavioral_probe fails and calls out the gap explicitly. Each probe
# returns the built CmdSpec so both assertions the spec must satisfy — the env
# var is stripped, and the caller's intent is stamped onto the artifact — are
# driven from one registry rather than two parallel builder lists.
_BEHAVIORAL_PROBES: dict[str, Callable[[bool], CmdSpec]] = {
    "build_headless_cmd": _headless_spec,
    "build_skill_session_cmd": _skill_session_spec,
    "build_food_truck_cmd": _food_truck_spec,
    "build_resume_cmd": _resume_spec,
    "build_interactive_cmd": _interactive_spec,
}


def _stripped(builder_name: str, force: bool) -> bool:
    return (
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in _BEHAVIORAL_PROBES[builder_name](force).env
    )


def test_every_discovered_builder_has_a_behavioral_probe() -> None:
    """Discovery must stay in sync with the behavioral probe registry."""
    missing = set(_discover_builder_names()) - set(_BEHAVIORAL_PROBES)
    assert not missing, (
        f"Builder(s) discovered but not covered by a behavioral probe: {missing}. "
        "Add an entry to _BEHAVIORAL_PROBES in this file."
    )


@pytest.mark.parametrize("builder_name", sorted(_BEHAVIORAL_PROBES))
def test_force_inactive_false_keeps_env_var(builder_name: str) -> None:
    """With force=False, the option does not strip the env var."""
    assert _stripped(builder_name, False) is False


@pytest.mark.parametrize("builder_name", sorted(_BEHAVIORAL_PROBES))
def test_force_inactive_true_strips_in_every_path(builder_name: str) -> None:
    """With force=True, every builder must strip the env var."""
    assert _stripped(builder_name, True) is True


@pytest.mark.parametrize("force", [True, False])
@pytest.mark.parametrize("builder_name", sorted(_BEHAVIORAL_PROBES))
def test_every_builder_stamps_intent_onto_the_spec(builder_name: str, force: bool) -> None:
    """The checkpoint reads intent off the spec, so every builder must record it."""
    assert _BEHAVIORAL_PROBES[builder_name](force).force_inactive_agent_teams is force


def test_build_cmd_preserves_every_headless_field_except_cwd() -> None:
    """build_cmd rebuilds its own headless spec; it must not drop fields."""
    backend = ClaudeCodeBackend()
    headless = backend.build_headless_cmd("/test")
    wrapped = backend.build_cmd("/test", "/work/repo")

    assert wrapped == replace(headless, cwd="/work/repo")
