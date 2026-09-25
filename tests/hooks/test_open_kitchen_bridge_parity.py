"""Parity checks for the standalone open-kitchen session-registry bridge."""

from __future__ import annotations

import io
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.core.runtime.session_registry import (
    bridge_claude_session_id,
    read_registry,
    registry_path,
)
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.medium]


def _write_registry(project_dir: Path, registry: dict[str, dict[str, object]]) -> Path:
    path = registry_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry), encoding="utf-8")
    return path


def _bridge(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    launch_id: str,
    session_id: str,
) -> None:
    from autoskillit.hooks._runtime._hook_settings import bridge_session_registry

    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", launch_id)
    bridge_session_registry(session_id, str(project_dir))


def _race_bridge(
    implementation: str,
    project_dir: str,
    launch_id: str,
    session_id: str,
    barrier: Any,
    outcomes: Any,
) -> None:
    from autoskillit.hooks._runtime._session_registry_bridge import bridge_session_registry

    project = Path(project_dir)
    if implementation == "hook":
        os.environ["AUTOSKILLIT_STATE_ROOT"] = project_dir
    barrier.wait(timeout=5)
    try:
        if implementation == "hook":
            bridge_session_registry(session_id, project_dir, launch_id=launch_id)
        else:
            bridge_claude_session_id(project, launch_id, session_id)
    except ValueError:
        outcomes.put((implementation, "refused"))
    else:
        outcomes.put((implementation, "success"))


def test_authenticated_cook_requires_one_persisted_native_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.hooks._runtime._hook_settings import (
        is_authenticated_top_level_cook,
    )

    registry_file = _write_registry(
        tmp_path,
        {
            "cook-launch": {
                "session_type": "cook",
                "claude_session_id": "native-session",
            }
        },
    )
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", "cook-launch")
    payload = {"session_id": "native-session", "cwd": str(tmp_path)}

    assert is_authenticated_top_level_cook(payload, str(tmp_path), "native-session")

    registry_file.write_text(
        json.dumps(
            {
                "cook-launch": {
                    "session_type": "cook",
                    "claude_session_id": "native-session",
                },
                "duplicate": {
                    "session_type": "cook",
                    "claude_session_id": "native-session",
                },
            }
        ),
        encoding="utf-8",
    )
    assert not is_authenticated_top_level_cook(payload, str(tmp_path), "native-session")

    registry_file.write_text(
        json.dumps(
            {
                "cook-launch": {
                    "session_type": "order",
                    "claude_session_id": "native-session",
                }
            }
        ),
        encoding="utf-8",
    )
    assert not is_authenticated_top_level_cook(payload, str(tmp_path), "native-session")


def test_authenticated_managed_codex_cook_requires_parent_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.hooks._runtime._hook_settings import (
        is_authenticated_top_level_cook,
    )
    from autoskillit.hooks._session_binding import (
        SessionBinding,
        resolve_binding_path,
        write_binding,
    )

    launch_id = "managed-cook"
    _write_registry(
        tmp_path,
        {launch_id: {"session_type": "cook", "claude_session_id": None}},
    )
    binding_path = resolve_binding_path(str(tmp_path), launch_id)
    binding = SessionBinding(
        schema_version=3,
        session_id=launch_id,
        join_required=True,
        binding_valid=True,
        artifact_digest="artifact",
        loaded_skills=(),
        managed_parent_id=launch_id,
        managed_route="interactive-parent",
        managed_guard_set=("join_followup_guard", "join_stop_guard"),
        managed_config_digest="config",
    )
    write_binding(binding_path, binding)
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", launch_id)
    monkeypatch.setenv("AUTOSKILLIT_MANAGED_JOIN_PARENT_ID", launch_id)
    payload = {"session_id": "codex-thread", "cwd": str(tmp_path)}

    assert is_authenticated_top_level_cook(payload, str(tmp_path), launch_id)

    write_binding(binding_path, binding._replace(managed_leaf_id="leaf"))
    assert not is_authenticated_top_level_cook(payload, str(tmp_path), launch_id)


def test_payload_cook_predicate_is_session_predicate_plus_payload_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autoskillit.hooks._runtime._session_registry_bridge import (
        is_authenticated_top_level_cook,
        is_authenticated_top_level_cook_session,
    )
    from autoskillit.hooks._session_binding import (
        SessionBinding,
        resolve_binding_path,
        write_binding,
    )

    launch_id = "cook-launch"
    session_id = "native-session"
    claude_env = {
        "headless": False,
        "backend": "claude-code",
        "launch_id": launch_id,
        "managed_parent_id": "",
    }
    payload_shapes = (
        ("top_level", {"session_id": session_id}),
        ("descendant", {"session_id": session_id, "agent_id": "child-agent"}),
        ("mismatched_session", {"session_id": "other-session"}),
    )
    session_states = (
        ("bridged_cook", "cook", True),
        ("order", "order", True),
        ("unbridged", "cook", False),
    )
    for state_name, session_type, bridged in session_states:
        project_dir = tmp_path / state_name
        _write_registry(
            project_dir,
            {launch_id: {"session_type": session_type, "claude_session_id": None}},
        )
        if bridged:
            _bridge(monkeypatch, project_dir, launch_id, session_id)
        session_authenticated = is_authenticated_top_level_cook_session(
            str(project_dir),
            session_id,
            **claude_env,
        )
        for payload_name, payload_identity in payload_shapes:
            payload = {"cwd": str(project_dir), **payload_identity}
            assert is_authenticated_top_level_cook(
                payload,
                str(project_dir),
                session_id,
                **claude_env,
            ) == (
                session_authenticated
                and not payload.get("agent_id")
                and payload.get("session_id") == session_id
            ), (state_name, payload_name)

    codex_project = tmp_path / "managed-codex"
    _write_registry(
        codex_project,
        {launch_id: {"session_type": "cook", "claude_session_id": None}},
    )
    binding = SessionBinding(
        schema_version=3,
        session_id=launch_id,
        join_required=True,
        binding_valid=True,
        artifact_digest="artifact",
        loaded_skills=(),
        managed_parent_id=launch_id,
        managed_route="interactive-parent",
        managed_guard_set=("join_followup_guard", "join_stop_guard"),
        managed_config_digest="config",
    )
    write_binding(resolve_binding_path(str(codex_project), launch_id), binding)
    codex_env = {
        "headless": False,
        "backend": "codex",
        "launch_id": launch_id,
        "managed_parent_id": launch_id,
    }
    payload = {"cwd": str(codex_project), "session_id": "payload-session-is-ignored"}
    assert is_authenticated_top_level_cook_session(
        str(codex_project),
        launch_id,
        **codex_env,
    )
    assert is_authenticated_top_level_cook(
        payload,
        str(codex_project),
        launch_id,
        **codex_env,
    )
    payload["agent_id"] = "child-agent"
    assert not is_authenticated_top_level_cook(
        payload,
        str(codex_project),
        launch_id,
        **codex_env,
    )


@pytest.mark.parametrize(
    ("registry", "session_id", "expected_message"),
    [
        (
            {"requested": {"claude_session_id": "old-session"}},
            "new-session",
            "Launch 'requested' is already bound to session 'old-session'; "
            "cannot bind 'new-session'",
        ),
        (
            {
                "claimed": {"claude_session_id": "shared-session"},
                "requested": {"claude_session_id": None},
            },
            "shared-session",
            "Session 'shared-session' is already claimed by launch 'claimed'; "
            "cannot assign it to launch 'requested'",
        ),
    ],
)
def test_bridge_rejects_non_unique_bindings_with_core_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registry: dict[str, dict[str, object]],
    session_id: str,
    expected_message: str,
) -> None:
    core_project = tmp_path / "core"
    hook_project = tmp_path / "hook"
    core_file = _write_registry(core_project, registry)
    registry_file = _write_registry(hook_project, registry)
    core_before = core_file.read_bytes()
    hook_before = registry_file.read_bytes()

    with pytest.raises(ValueError) as core_error:
        bridge_claude_session_id(core_project, "requested", session_id)
    with pytest.raises(ValueError) as hook_error:
        _bridge(monkeypatch, hook_project, "requested", session_id)

    assert str(hook_error.value) == str(core_error.value) == expected_message
    assert core_file.read_bytes() == core_before
    assert registry_file.read_bytes() == hook_before
    assert read_registry(core_project) == read_registry(hook_project) == registry


def test_bridge_is_idempotent_for_its_existing_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_file = _write_registry(
        tmp_path,
        {
            "requested": {
                "claude_session_id": "session-123",
                "owner_pid": 123,
                "session_type": "cook",
            },
            "unrelated": {"claude_session_id": None, "recipe_name": "preserved"},
        },
    )
    before = registry_file.read_bytes()

    _bridge(monkeypatch, tmp_path, "requested", "session-123")

    assert registry_file.read_bytes() == before


def test_bridge_preserves_existing_launch_metadata_and_other_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_file = _write_registry(
        tmp_path,
        {
            "requested": {
                "claude_session_id": None,
                "claimant_pid": os.getpid() + 10_000,
                "claimant_boot_id": "claimant-boot",
                "claimant_starttime_ticks": 42,
                "launched_at": "2026-09-17T00:00:00+00:00",
                "owner_pid": 123,
                "recipe_name": "implementation",
                "session_type": "cook",
            },
            "unrelated": {
                "claude_session_id": None,
                "owner_pid": 456,
                "recipe_name": "research",
            },
        },
    )

    _bridge(monkeypatch, tmp_path, "requested", "session-123")

    updated = json.loads(registry_file.read_text(encoding="utf-8"))
    assert updated["requested"] == {
        "claude_session_id": "session-123",
        "claimant_pid": os.getpid() + 10_000,
        "claimant_boot_id": "claimant-boot",
        "claimant_starttime_ticks": 42,
        "launched_at": "2026-09-17T00:00:00+00:00",
        "owner_pid": 123,
        "recipe_name": "implementation",
        "session_type": "cook",
    }
    assert updated["unrelated"] == {
        "claude_session_id": None,
        "owner_pid": 456,
        "recipe_name": "research",
    }


def test_core_and_hook_bridges_contend_on_one_session_claim(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    _write_registry(
        project_dir,
        {
            "core-launch": {"claude_session_id": None},
            "hook-launch": {"claude_session_id": None},
        },
    )
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_race_bridge,
            args=(
                implementation,
                str(project_dir),
                launch_id,
                "shared-session",
                barrier,
                outcomes,
            ),
        )
        for implementation, launch_id in (
            ("core", "core-launch"),
            ("hook", "hook-launch"),
        )
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=10)
        assert [process.exitcode for process in processes] == [0, 0]
        assert sorted(outcomes.get(timeout=1)[1] for _process in processes) == [
            "refused",
            "success",
        ]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    registry = read_registry(project_dir)
    claimants = [
        launch_id
        for launch_id, row in registry.items()
        if row.get("claude_session_id") == "shared-session"
    ]
    assert len(claimants) == 1


def test_guard_main_reports_bridge_conflict_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.hooks.guards import open_kitchen_guard

    project_dir = tmp_path / "project"
    _write_registry(project_dir, {"requested": {"claude_session_id": "old-session"}})
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", "requested")
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(project_dir))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "cwd": str(project_dir),
                    "session_id": "new-session",
                    "tool_input": {},
                }
            )
        ),
    )

    with pytest.raises(SystemExit) as exit_info:
        open_kitchen_guard.main()

    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "[open_kitchen_guard] registry bridge failed: Launch 'requested' is already bound "
        "to session 'old-session'; cannot bind 'new-session'"
    ) in captured.err
    marker_path = project_dir / ".autoskillit" / "temp" / "kitchen_state" / "new-session.json"
    assert marker_path.is_file()


def _start_lock_holder(lock_path: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, os, sys\n"
                "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "sys.stdin.readline()\n"
                "fcntl.flock(fd, fcntl.LOCK_UN)\n"
                "os.close(fd)\n"
            ),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=production_interpreter_env(),
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked"
    return holder


def test_bridge_rereads_under_the_core_shared_lock_before_writing(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    foreign_cwd = tmp_path / "foreign"
    foreign_cwd.mkdir()
    registry_file = _write_registry(
        project_dir,
        {
            "requested": {"claude_session_id": None, "owner_pid": 123},
            "unrelated": {"claude_session_id": None, "recipe_name": "original"},
        },
    )
    holder = _start_lock_holder(registry_file.with_suffix(".lock"))
    hook: subprocess.Popen[str] | None = None

    env = {
        key: value
        for key, value in production_interpreter_env().items()
        if key
        not in {
            "AUTOSKILLIT_HEADLESS",
            "AUTOSKILLIT_LAUNCH_ID",
            "AUTOSKILLIT_STATE_DIR",
            "AUTOSKILLIT_STATE_ROOT",
        }
    }
    env.update(
        AUTOSKILLIT_LAUNCH_ID="requested",
        AUTOSKILLIT_STATE_ROOT=str(project_dir),
    )

    try:
        hook = subprocess.Popen(
            [sys.executable, str(pkg_root() / "hooks" / "guards" / "open_kitchen_guard.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=foreign_cwd,
        )
        assert hook.stdin is not None
        hook.stdin.write(
            json.dumps(
                {
                    "cwd": str(project_dir),
                    "session_id": "session-123",
                    "tool_input": {},
                }
            )
        )
        hook.stdin.close()
        hook.stdin = None

        marker_path = project_dir / ".autoskillit" / "temp" / "kitchen_state" / "session-123.json"
        deadline = time.monotonic() + 5
        while not marker_path.exists() and hook.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker_path.is_file()
        assert hook.poll() is None

        registry = json.loads(registry_file.read_text(encoding="utf-8"))
        registry["unrelated"]["recipe_name"] = "written-while-hook-waited"
        registry_file.write_text(json.dumps(registry), encoding="utf-8")

        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.stdin = None
        holder.wait(timeout=5)

        stdout, stderr = hook.communicate(timeout=5)
        assert hook.returncode == 0, stderr
        assert stdout == ""

        updated = json.loads(registry_file.read_text(encoding="utf-8"))
        assert updated["requested"] == {"claude_session_id": "session-123", "owner_pid": 123}
        assert updated["unrelated"] == {
            "claude_session_id": None,
            "recipe_name": "written-while-hook-waited",
        }
    finally:
        if holder.poll() is None:
            if holder.stdin is not None:
                holder.stdin.write("\n")
                holder.stdin.close()
                holder.stdin = None
            holder.wait(timeout=5)
        if hook is not None and hook.poll() is None:
            hook.terminate()
            hook.wait(timeout=5)
