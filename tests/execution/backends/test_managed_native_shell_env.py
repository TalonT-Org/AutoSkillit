"""Managed native-shell environment ownership at backend launch boundaries."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import (
    MANAGED_ATTEMPT_ID_ENV_VAR,
    MANAGED_LAUNCH_ID_ENV_VAR,
    MANAGED_LINEAGE_DIGEST_ENV_VAR,
    MANAGED_LINEAGE_REF_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureMode,
    SkillSessionConfig,
    resolve_native_shell_capture_decision,
)
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_ATTEMPT_ID = "c" * 32
_PROTECTED_KEYS = frozenset(
    {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    }
)


@pytest.fixture(autouse=True)
def _clear_managed_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _PROTECTED_KEYS:
        monkeypatch.delenv(key, raising=False)


def _managed_values():
    decision = resolve_native_shell_capture_decision(NativeShellCaptureMode.DIRECT)
    lineage_ref = ManagedHeadlessSessionLineageRef(
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor="/tmp/autoskillit-managed-lineage",
        anchor_device=11,
        anchor_inode=22,
    )
    return decision, lineage_ref


def _expected_env() -> dict[str, str]:
    decision, lineage_ref = _managed_values()
    return {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR: decision.mode.value,
        MANAGED_LAUNCH_ID_ENV_VAR: lineage_ref.launch_id,
        MANAGED_ATTEMPT_ID_ENV_VAR: _ATTEMPT_ID,
        MANAGED_LINEAGE_DIGEST_ENV_VAR: lineage_ref.lineage_digest,
        MANAGED_LINEAGE_REF_ENV_VAR: json.dumps(
            lineage_ref.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _hostile_env() -> dict[str, str]:
    return dict.fromkeys(_PROTECTED_KEYS, "hostile")


def _assert_authoritative_managed_env(env) -> None:
    assert {key: env[key] for key in _PROTECTED_KEYS} == _expected_env()


def _assert_no_managed_env(env) -> None:
    assert _PROTECTED_KEYS.isdisjoint(env)


def test_codex_managed_skill_env_overrides_hostile_provider_extras() -> None:
    decision, lineage_ref = _managed_values()
    backend = CodexBackend()

    spec = backend.build_skill_session_cmd(
        "/autoskillit:test",
        "/tmp/project",
        SkillSessionConfig(
            provider_extras=_hostile_env(),
            native_shell_capture_decision=decision,
            managed_lineage_ref=lineage_ref,
            managed_attempt_id=_ATTEMPT_ID,
        ),
    )

    _assert_authoritative_managed_env(spec.env)


def test_codex_managed_food_truck_env_overrides_hostile_extras() -> None:
    decision, lineage_ref = _managed_values()
    backend = CodexBackend()

    spec = backend.build_food_truck_cmd(
        orchestrator_prompt="dispatch",
        plugin_binding=None,
        cwd="/tmp/project",
        completion_marker="DONE",
        env_extras=_hostile_env(),
        native_shell_capture_decision=decision,
        managed_lineage_ref=lineage_ref,
        managed_attempt_id=_ATTEMPT_ID,
    )

    _assert_authoritative_managed_env(spec.env)


def test_codex_managed_resume_env_overrides_hostile_extras() -> None:
    decision, lineage_ref = _managed_values()
    backend = CodexBackend()

    spec = backend.build_resume_cmd(
        resume_session_id="thread-1",
        prompt="continue",
        env_extras=_hostile_env(),
        native_shell_capture_decision=decision,
        managed_lineage_ref=lineage_ref,
        managed_attempt_id=_ATTEMPT_ID,
    )

    _assert_authoritative_managed_env(spec.env)


def test_codex_unmanaged_builders_do_not_inject_managed_env() -> None:
    backend = CodexBackend()
    hostile_env = _hostile_env()

    specs = (
        backend.build_headless_cmd("prompt", env_extras=hostile_env),
        backend.build_skill_session_cmd(
            "/autoskillit:test",
            "/tmp/project",
            SkillSessionConfig(provider_extras=hostile_env),
        ),
        backend.build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=None,
            cwd="/tmp/project",
            completion_marker="DONE",
            env_extras=hostile_env,
        ),
        backend.build_resume_cmd(
            resume_session_id="thread-1",
            prompt="continue",
            env_extras=hostile_env,
        ),
    )

    for spec in specs:
        _assert_no_managed_env(spec.env)


def test_codex_interactive_cook_declares_capture_mode_only() -> None:
    """The cook launch path is structurally unmanaged but now declaredly so.

    build_interactive_cmd injects the capture-mode declaration positively
    (so shell_capture_hook.py no longer infers "normal" from absence), while
    still carrying none of the four managed-identity vars — those remain
    exclusive to the managed builders above.
    """
    backend = CodexBackend()
    hostile_env = _hostile_env()

    spec = backend.build_interactive_cmd(env_extras=hostile_env)

    assert spec.env[NATIVE_SHELL_CAPTURE_MODE_ENV_VAR] == "capture"
    identity_keys = _PROTECTED_KEYS - {NATIVE_SHELL_CAPTURE_MODE_ENV_VAR}
    assert identity_keys.isdisjoint(spec.env)


def test_codex_ambient_protected_controls_do_not_reach_any_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _hostile_env().items():
        monkeypatch.setenv(key, value)
    backend = CodexBackend()

    specs = (
        backend.build_cmd("prompt", "/tmp/project"),
        backend.build_headless_cmd("prompt"),
        backend.build_skill_session_cmd(
            "/autoskillit:test",
            "/tmp/project",
            SkillSessionConfig(),
        ),
        backend.build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=None,
            cwd="/tmp/project",
            completion_marker="DONE",
        ),
        backend.build_resume_cmd(
            resume_session_id="thread-1",
            prompt="continue",
        ),
    )

    for spec in specs:
        _assert_no_managed_env(spec.env)

    interactive_spec = backend.build_interactive_cmd()
    assert interactive_spec.env[NATIVE_SHELL_CAPTURE_MODE_ENV_VAR] == "capture"
    identity_keys = _PROTECTED_KEYS - {NATIVE_SHELL_CAPTURE_MODE_ENV_VAR}
    assert identity_keys.isdisjoint(interactive_spec.env)


def test_claude_managed_parameters_never_inject_managed_env() -> None:
    decision, lineage_ref = _managed_values()
    backend = ClaudeCodeBackend()

    specs = (
        backend.build_skill_session_cmd(
            "/autoskillit:test",
            "/tmp/project",
            SkillSessionConfig(
                provider_extras=_hostile_env(),
                native_shell_capture_decision=decision,
                managed_lineage_ref=lineage_ref,
                managed_attempt_id=_ATTEMPT_ID,
            ),
        ),
        backend.build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=None,
            cwd="/tmp/project",
            completion_marker="DONE",
            env_extras=_hostile_env(),
            native_shell_capture_decision=decision,
            managed_lineage_ref=lineage_ref,
            managed_attempt_id=_ATTEMPT_ID,
        ),
        backend.build_resume_cmd(
            resume_session_id="session-1",
            prompt="continue",
            env_extras=_hostile_env(),
            native_shell_capture_decision=decision,
            managed_lineage_ref=lineage_ref,
            managed_attempt_id=_ATTEMPT_ID,
        ),
    )

    for spec in specs:
        _assert_no_managed_env(spec.env)
