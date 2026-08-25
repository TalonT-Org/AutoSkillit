"""Tests for core protocol contracts."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from autoskillit.core.types import (
    CIRunScope,
    SkillUnavailabilityPayload,
    ValidatedAddDir,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_managed_session_home_frozen_slots_exact_fields_and_exports(tmp_path) -> None:
    import autoskillit.core as core
    from autoskillit.core import (
        ManagedSessionHome,
    )
    from autoskillit.core.types._type_results import __all__ as results_all

    skills_dir = ValidatedAddDir(tmp_path / "home" / "skills")
    unavailability_payload: SkillUnavailabilityPayload = {
        "backend": "codex",
        "unavailable": (),
    }
    handle = ManagedSessionHome(
        launch_id="launch-1",
        generated_home=tmp_path / "home",
        skills_dir=skills_dir,
        pass_fds=(3, 5),
        unavailability_payload=unavailability_payload,
    )

    assert tuple(field.name for field in dataclasses.fields(ManagedSessionHome)) == (
        "launch_id",
        "generated_home",
        "skills_dir",
        "pass_fds",
        "unavailability_payload",
    )
    assert get_type_hints(ManagedSessionHome) == {
        "launch_id": str,
        "generated_home": Path,
        "skills_dir": ValidatedAddDir,
        "pass_fds": tuple[int, ...],
        "unavailability_payload": SkillUnavailabilityPayload,
    }
    assert handle.unavailability_payload is unavailability_payload
    assert set(handle.unavailability_payload) == {"backend", "unavailable"}
    assert isinstance(handle.unavailability_payload, Mapping)
    assert not hasattr(handle, "__dict__")
    assert "ManagedSessionHome" in results_all
    assert "ManagedSessionHome" in core.__all__  # type: ignore[attr-defined]
    assert "SkillUnavailabilityPayload" in results_all
    assert "SkillUnavailabilityPayload" in core.__all__  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        handle.launch_id = "other"  # type: ignore[misc]


def test_github_fetcher_protocol_has_label_methods() -> None:
    import inspect

    from autoskillit.core.types import GitHubFetcher

    members = {name for name, _ in inspect.getmembers(GitHubFetcher)}
    assert "add_labels" in members
    assert "remove_label" in members
    assert "ensure_label" in members


def test_subprocess_result_has_elapsed_seconds_field() -> None:
    """SubprocessResult must carry a pre-computed monotonic elapsed_seconds."""
    from autoskillit.core.types import SubprocessResult, TerminationReason

    result = SubprocessResult(
        returncode=0,
        stdout="",
        stderr="",
        termination=TerminationReason.COMPLETED,
        pid=1,
    )
    assert hasattr(result, "elapsed_seconds")
    assert result.elapsed_seconds == 0.0
    result2 = dataclasses.replace(result, elapsed_seconds=7.3)
    assert result2.elapsed_seconds == pytest.approx(7.3)


# ---------------------------------------------------------------------------
# P10-F1 — SubprocessRunner.pty_mode default
# ---------------------------------------------------------------------------


def test_subprocess_runner_protocol_pty_mode_default_false() -> None:
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    assert sig.parameters["pty_mode"].default is False


# ---------------------------------------------------------------------------
# P2-A6 — SubprocessRunner marker_dir and session_id params
# ---------------------------------------------------------------------------


def test_subprocess_runner_protocol_marker_dir_default_none() -> None:
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    assert sig.parameters["marker_dir"].default is None


def test_subprocess_runner_protocol_session_id_default_none() -> None:
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    assert sig.parameters["session_id"].default is None


def test_subprocess_runner_protocol_marker_params_after_max_extension() -> None:
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    params = list(sig.parameters)
    max_ext_idx = params.index("max_extension_seconds")
    marker_idx = params.index("marker_dir")
    session_idx = params.index("session_id")
    assert marker_idx == max_ext_idx + 1, (
        f"marker_dir must immediately follow max_extension_seconds, "
        f"got indices {max_ext_idx} and {marker_idx}"
    )
    assert session_idx == marker_idx + 1, (
        f"session_id must immediately follow marker_dir, "
        f"got indices {marker_idx} and {session_idx}"
    )


def test_subprocess_runner_protocol_marker_params_are_keyword_only() -> None:
    import inspect

    from autoskillit.core import SubprocessRunner

    sig = inspect.signature(SubprocessRunner.__call__)
    for name in ("marker_dir", "session_id"):
        param = sig.parameters[name]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must be keyword-only, got {param.kind.name}"
        )


# ---------------------------------------------------------------------------
# CIRunScope event field
# ---------------------------------------------------------------------------


def test_ci_run_scope_event_field() -> None:
    """CIRunScope must accept and store an event field."""
    scope = CIRunScope(event="push")
    assert scope.event == "push"
    assert scope.workflow is None
    assert scope.head_sha is None


def test_ci_run_scope_event_defaults_to_none() -> None:
    """CIRunScope.event defaults to None when not specified."""
    scope = CIRunScope()
    assert scope.event is None
