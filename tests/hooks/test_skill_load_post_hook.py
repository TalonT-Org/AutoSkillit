"""Tests for skill_load_post_hook.py PostToolUse hook."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_FLAG_RELPATH = ".autoskillit/temp/skill_guard_abc123.flag"


def _write_join_bearing_projection_manifest(
    project_root: Path,
    *,
    skill_name: str,
    join_required: bool = True,
    artifact_digest: str = "artdigest-1",
    artifact_incarnation: str = "2026-08-15T12:00:00Z/inc-7",
    semantic_digest: str = "sem-1",
    adaptation_digest: str = "adapt-1",
    projected_digest: str = "proj-1",
    canonical_digest: str = "canon-1",
    child_spawn_cardinality: dict[str, object] | None = None,
) -> Path:
    """Pre-populate a projection manifest sidecar so the hook picks it up.

    The hook walks ``.claude/plugins/installed/`` looking for sibling
    ``.{plugin_dir}.autoskillit-projection.json`` files. Drop a manifest
    in the most direct location.
    """
    plugins_root = project_root / ".claude" / "plugins" / "installed" / "join-plugin"
    plugins_root.mkdir(parents=True, exist_ok=True)
    manifest_path = plugins_root.parent / f".{plugins_root.name}.autoskillit-projection.json"
    payload: dict[str, object] = {
        "schema_version": 1,
        "skills": {
            skill_name: {
                "join_required": join_required,
                "semantic_digest": semantic_digest,
                "adaptation_digest": adaptation_digest,
                "projected_digest": projected_digest,
                "canonical_digest": canonical_digest,
                "artifact_digest": artifact_digest,
                "artifact_incarnation": artifact_incarnation,
                "child_spawn_cardinality": (
                    child_spawn_cardinality
                    if child_spawn_cardinality is not None
                    else {"explicit_slots": 4}
                ),
            }
        },
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def _run_hook(
    *,
    stdin_data: dict | str,
    tmp_dir: Path,
    provider_profile: str | None = None,
    agent_backend: str | None = "claude-code",
) -> tuple[str, int]:
    """Run skill_load_post_hook.main(), return (stdout, exit_code)."""
    from autoskillit.hooks.skill_load_post_hook import main  # noqa: PLC0415

    stdin_content = stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data)

    env_base = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AUTOSKILLIT_PROVIDER_PROFILE", "AUTOSKILLIT_AGENT_BACKEND")
    }
    if provider_profile is not None:
        env_base["AUTOSKILLIT_PROVIDER_PROFILE"] = provider_profile
    if agent_backend is not None:
        env_base["AUTOSKILLIT_AGENT_BACKEND"] = agent_backend

    buf = io.StringIO()
    exit_code = 0
    with (
        patch.dict(os.environ, env_base, clear=True),
        contextlib.redirect_stdout(buf),
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)),
        unittest.mock.patch(
            "autoskillit.hooks.skill_load_post_hook.Path.cwd", return_value=tmp_dir
        ),
    ):
        try:
            main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return buf.getvalue(), exit_code


def _make_skill_event(
    session_id: str = "abc123",
    skill: str = "implement-worktree-no-merge",
    agent_id: str | None = None,
) -> dict:
    event = {
        "tool_name": "Skill",
        "tool_input": {"skill": skill},
        "session_id": session_id,
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def test_writes_flag_when_provider_profile_set(tmp_path: Path) -> None:
    """T1-1: Flag file written with skill name when provider profile is set."""
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert flag.exists(), "Flag file must be written"
    assert "implement-worktree-no-merge" in flag.read_text()


def test_skips_when_provider_profile_empty(tmp_path: Path) -> None:
    """T1-2: No flag file when provider profile is not set."""
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile=None,
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists(), "Flag file must NOT be created when provider profile is empty"


def test_skips_for_non_skill_tool(tmp_path: Path) -> None:
    """T1-3: No flag file for non-Skill tool."""
    event = {
        "tool_name": "Read",
        "tool_input": {"file_path": "/foo"},
        "session_id": "abc123",
    }
    _run_hook(
        stdin_data=event,
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists()


def test_survives_malformed_stdin(tmp_path: Path) -> None:
    """T1-4: Exit 0 on malformed JSON."""
    _, exit_code = _run_hook(
        stdin_data="not valid json",
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    assert exit_code == 0


def test_skips_when_session_id_absent(tmp_path: Path) -> None:
    """T1-5: No flag file when session_id is missing."""
    event = {
        "tool_name": "Skill",
        "tool_input": {"skill": "make-plan"},
    }
    _run_hook(
        stdin_data=event,
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag_dir = tmp_path / ".autoskillit" / "temp"
    if flag_dir.exists():
        flags = list(flag_dir.glob("skill_guard_*.flag"))
        assert not flags, "No flag file should be created when session_id is absent"


def _run_hook_with_marker(
    *,
    stdin_data: dict | str,
    tmp_dir: Path,
    provider_profile: str | None = None,
    agent_backend: str | None = "claude-code",
    completion_marker: str | None = None,
) -> tuple[str, int]:
    """Run skill_load_post_hook.main() with AUTOSKILLIT_COMPLETION_MARKER support."""
    from autoskillit.hooks.skill_load_post_hook import main  # noqa: PLC0415

    stdin_content = stdin_data if isinstance(stdin_data, str) else json.dumps(stdin_data)

    env_base = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "AUTOSKILLIT_PROVIDER_PROFILE",
            "AUTOSKILLIT_AGENT_BACKEND",
            "AUTOSKILLIT_COMPLETION_MARKER",
        )
    }
    if provider_profile is not None:
        env_base["AUTOSKILLIT_PROVIDER_PROFILE"] = provider_profile
    if agent_backend is not None:
        env_base["AUTOSKILLIT_AGENT_BACKEND"] = agent_backend
    if completion_marker is not None:
        env_base["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker

    buf = io.StringIO()
    exit_code = 0
    with (
        patch.dict(os.environ, env_base, clear=True),
        contextlib.redirect_stdout(buf),
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)),
        unittest.mock.patch(
            "autoskillit.hooks.skill_load_post_hook.Path.cwd", return_value=tmp_dir
        ),
    ):
        try:
            main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return buf.getvalue(), exit_code


def test_emits_additional_context_when_completion_marker_set(tmp_path: Path) -> None:
    """T1-6: When AUTOSKILLIT_COMPLETION_MARKER is set, hook emits additionalContext JSON."""
    marker = "%%ORDER_UP::abc12345%%"
    stdout, exit_code = _run_hook_with_marker(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        completion_marker=marker,
    )
    assert exit_code == 0
    assert stdout.strip(), "Hook must emit additionalContext to stdout"
    payload = json.loads(stdout)
    assert "additionalContext" in payload
    assert marker in payload["additionalContext"]


def test_skips_flag_write_when_agent_id_present(tmp_path: Path) -> None:
    """T1-7: No flag written when agent_id is present (subagent context).

    A nested child re-loading a join-bearing skill must NOT recreate the
    parent session's binding. The agent_id short-circuit is the
    subagent-context exemption; the parent binding already carries the
    join_required bit OR-accumulated from the original load.
    """
    _run_hook(
        stdin_data=_make_skill_event(agent_id="agent-uuid-123"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag_dir = tmp_path / ".autoskillit" / "temp"
    flags = list(flag_dir.glob("skill_guard_*.flag"))
    assert not flags, "No flag file should be created in subagent context"


def test_skips_flag_write_when_agent_id_present_join_bearing_skill(
    tmp_path: Path,
) -> None:
    """REQ-053: Subagent re-load of a join-bearing skill never recreates the flag.

    Even when the projection manifest reports join_required=true for the
    nested child, the agent_id short-circuit must win — the parent's
    existing binding is the authoritative join record, and a child-side
    re-load must not produce a new flag that would otherwise orphan the
    parent's join ledger key.
    """
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="join-bearing-skill",
        join_required=True,
    )
    _run_hook(
        stdin_data=_make_skill_event(skill="join-bearing-skill", agent_id="child-1"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag_dir = tmp_path / ".autoskillit" / "temp"
    flags = list(flag_dir.glob("skill_guard_*.flag"))
    assert not flags, (
        "Subagent re-load of a join-bearing skill must NOT create a new "
        "flag — the parent's binding is authoritative"
    )


def test_writes_flag_to_project_root_via_ancestor_walk(tmp_path: Path) -> None:
    """T1-8: Flag written to project root when CWD is a subdirectory."""
    project = tmp_path / "project"
    (project / ".autoskillit").mkdir(parents=True)

    deep_cwd = project / "sub" / "deep"
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=deep_cwd,
        provider_profile="minimax",
    )
    flag = project / ".autoskillit" / "temp" / "skill_guard_abc123.flag"
    assert flag.exists(), "Flag must be written to project root, not CWD"
    assert "implement-worktree-no-merge" in flag.read_text()


@pytest.mark.parametrize(
    ("agent_backend", "expected_flag"),
    [
        ("codex", False),
        ("claude-code", True),
        (None, True),
        ("unexpected", True),
    ],
    ids=[
        "codex_bypasses_flag_write",
        "claude-code_writes_flag",
        "unset_backend_writes_flag",
        "unrecognized_backend_writes_flag",
    ],
)
def test_skill_load_post_hook_backend_authority(
    tmp_path: Path, agent_backend: str | None, expected_flag: bool
) -> None:
    """Backend identity is the primary gate; provider profile is secondary.

    Codex backend must NEVER trigger the skill-load flag even when the
    provider profile would otherwise suggest a provider-aware session.
    Unset and unrecognized backends do not silently inherit Codex's
    exemption — they fall through to the existing profile check.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        agent_backend=agent_backend,
    )
    flag = tmp_path / _FLAG_RELPATH
    if expected_flag:
        assert flag.exists(), "Flag file must be written for non-Codex backends"
    else:
        assert not flag.exists(), "Flag file must NOT be written for Codex backend"


@pytest.mark.parametrize(
    ("agent_backend", "expected_flag"),
    [
        ("codex", False),
        ("claude-code", True),
        (None, True),
        ("unexpected", True),
    ],
    ids=[
        "codex_bypasses_join_flag",
        "claude-code_writes_join_flag",
        "unset_backend_writes_join_flag",
        "unrecognized_backend_writes_join_flag",
    ],
)
def test_skill_load_post_hook_backend_authority_join_bearing(
    tmp_path: Path, agent_backend: str | None, expected_flag: bool
) -> None:
    """REQ-053: Backend authority holds for join-bearing skills too.

    A join-bearing skill projection produces the same backend-gated
    behavior: Codex must NEVER write the flag (and therefore never
    admit a join), and Claude must write it. The projection manifest
    sidecar pre-populates the join_required bit.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="implement-worktree-no-merge",
        join_required=True,
    )
    _run_hook(
        stdin_data=_make_skill_event(skill="implement-worktree-no-merge"),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        agent_backend=agent_backend,
    )
    flag = tmp_path / _FLAG_RELPATH
    if expected_flag:
        assert flag.exists(), (
            "Flag file must be written for non-Codex backends even with a join-bearing skill"
        )
    else:
        assert not flag.exists(), (
            "Codex must NEVER write the join-bearing flag — it does not "
            "attest fixed_set_join_capable"
        )


def test_codex_bypass_with_nonempty_profile_writes_no_flag(tmp_path: Path) -> None:
    """The specific bug case: Codex + non-empty Anthropic profile → no flag."""
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        agent_backend="codex",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists(), "Backend check must win over provider profile"


def test_codex_bypass_join_bearing_skill_with_nonempty_profile_writes_no_flag(
    tmp_path: Path,
) -> None:
    """REQ-053: Codex + non-empty profile + join-bearing projection → no flag.

    Codex's capability attestation refuses REQUIRED_JOIN at admission.
    A join-bearing skill load must therefore NEVER produce the binding
    flag — otherwise downstream join gates would key off a binding
    Codex never honors.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="implement-worktree-no-merge",
        join_required=True,
    )
    _run_hook(
        stdin_data=_make_skill_event(skill="implement-worktree-no-merge"),
        tmp_dir=tmp_path,
        provider_profile="anthropic",
        agent_backend="codex",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert not flag.exists(), (
        "Codex backend must never write the flag for a join-bearing skill — "
        "backend check wins over the join-bearing projection"
    )


def test_unrecognized_backend_does_not_inherit_codex_exemption(tmp_path: Path) -> None:
    """An unrecognized backend + non-empty profile must still write the flag.

    Unknown/future backend values fall through to the profile check
    rather than being silently exempted as if they were Codex.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
        agent_backend="future-backend",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert flag.exists(), "Unrecognized backend must not silently bypass the flag write"


def test_join_bearing_skill_load_writes_complete_json_envelope(tmp_path: Path) -> None:
    """REQ-052: A join-bearing skill load writes a complete atomic JSON envelope.

    The hook reads the projection manifest sidecar and writes a flag
    file whose JSON envelope carries the full documented identity:
    skill_name, join_required, semantic/adaptation/projected/artifact
    digests, artifact_incarnation, child_spawn_cardinality, and
    binding_valid=true. Every required field is asserted.
    """
    manifest = _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="implement-worktree-no-merge",
        join_required=True,
        artifact_digest="art-abc",
        artifact_incarnation="2026-08-15T12:00:00Z/inc-7",
        semantic_digest="sem-xyz",
        adaptation_digest="adapt-xyz",
        projected_digest="proj-xyz",
        canonical_digest="canon-xyz",
        child_spawn_cardinality={"explicit_slots": 4, "max_inflight": 4},
    )
    assert manifest.exists(), "Helper must produce a manifest sidecar"

    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(skill="implement-worktree-no-merge"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / _FLAG_RELPATH
    assert flag.exists(), "Flag must be written for join-bearing Claude load"

    # Atomic JSON envelope — parse cleanly without manual coercion.
    payload = json.loads(flag.read_text())
    assert payload["schema_version"] == 1
    assert payload["session_id"] == "abc123"
    assert payload["join_required"] is True
    assert payload["binding_valid"] is True

    # Exactly one loaded-skill entry was appended.
    assert isinstance(payload["loaded_skills"], list)
    assert len(payload["loaded_skills"]) == 1
    entry = payload["loaded_skills"][0]
    assert entry["skill_name"] == "implement-worktree-no-merge"
    assert entry["join_required"] is True
    assert entry["semantic_digest"] == "sem-xyz"
    assert entry["adaptation_digest"] == "adapt-xyz"
    assert entry["projected_digest"] == "proj-xyz"
    assert entry["artifact_digest"] == "art-abc"
    assert entry["artifact_incarnation"] == "2026-08-15T12:00:00Z/inc-7"
    assert entry["binding_valid"] is True
    assert entry["child_spawn_cardinality"] == {"explicit_slots": 4, "max_inflight": 4}


def test_join_false_skill_load_keeps_join_required_false(tmp_path: Path) -> None:
    """REQ-052: A join-false skill load writes join_required=false explicitly.

    When the projection manifest reports join_required=false, the
    atomic envelope must carry that bit explicitly — never silently
    default to True. Downstream join gates (background_exec_guard,
    can_release_stop) key off this bit to allow or deny dispatch.
    """
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="implement-worktree-no-merge",
        join_required=False,
    )
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / _FLAG_RELPATH
    payload = json.loads(flag.read_text())
    assert payload["join_required"] is False
    assert payload["binding_valid"] is True
    assert payload["loaded_skills"][0]["join_required"] is False


def test_subsequent_join_false_load_does_not_downgrade_join_required(
    tmp_path: Path,
) -> None:
    """REQ-103: A later join-false Skill load must NOT downgrade join_required.

    The OR-accumulated monotonic bit means once a join-required skill
    has loaded in this session, every subsequent load — including a
    join-false one — leaves the session in the join-bound state. This
    is the documented monotonic contract that prevents a join gate
    bypass via a nested child loading a non-join skill.
    """
    (tmp_path / ".autoskillit").mkdir(parents=True)
    # First load: join-bearing.
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="first-skill",
        join_required=True,
    )
    _run_hook(
        stdin_data=_make_skill_event(skill="first-skill", session_id="downgrade-test"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    # Second load: join-false.
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="second-skill",
        join_required=False,
    )
    _run_hook(
        stdin_data=_make_skill_event(skill="second-skill", session_id="downgrade-test"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / ".autoskillit" / "temp" / "skill_guard_downgrade-test.flag"
    payload = json.loads(flag.read_text())
    # Monotonic OR — the join-false second load does not downgrade.
    assert payload["join_required"] is True, (
        "A join-false load must not downgrade an established join_required=true"
    )
    # Both entries remain in loaded_skills, in load order.
    assert [s["skill_name"] for s in payload["loaded_skills"]] == [
        "first-skill",
        "second-skill",
    ]


def test_fresh_session_without_join_loads_reports_join_required_false(
    tmp_path: Path,
) -> None:
    """REQ-103: A fresh session with no prior loads reports join_required=false.

    A session that has loaded a non-join skill (or has not loaded any
    skill at all) must not be globally blocked. The binding carries
    join_required=false and binding_valid=true so legitimate
    named/team dispatch proceeds normally.
    """
    _write_join_bearing_projection_manifest(
        tmp_path,
        skill_name="non-join-skill",
        join_required=False,
    )
    (tmp_path / ".autoskillit").mkdir(parents=True)
    _run_hook(
        stdin_data=_make_skill_event(skill="non-join-skill", session_id="fresh"),
        tmp_dir=tmp_path,
        provider_profile="minimax",
    )
    flag = tmp_path / ".autoskillit" / "temp" / "skill_guard_fresh.flag"
    assert flag.exists()
    payload = json.loads(flag.read_text())
    assert payload["join_required"] is False
    assert payload["binding_valid"] is True
    assert payload["loaded_skills"][0]["join_required"] is False
