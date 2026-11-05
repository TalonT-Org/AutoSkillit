"""Shared harness for git_ops_guard tests: payloads, a guard runner and repositories.

Members: _make_clean_env, _build_payload, _run_guard, _guard_exit_code,
_is_denied, _checked_out_ref_result, _guard_verdict, _git, build_linked_repo.

``UNSET`` distinguishes "use the default" from an explicit ``""`` or ``None`` for
the payload's cwd fields, so a test can send a relative or empty cwd.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest.mock
from pathlib import Path

import pytest

from autoskillit.hooks.guards import git_ops_guard
from tests._git_topology import add_linked_worktree, commit_file, head, init_checkout

UNSET: object = object()

_TOOL_NAME = "mcp__autoskillit__local__autoskillit__run_cmd"
_BASH_TOOL_NAME = "Bash"
_HOOK_CONFIG_RELPATH = ".autoskillit/temp/.hook_config.json"


def _make_clean_env(
    skill_name: str | None,
    session_type: str | None = None,
    headless: bool = True,
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()}
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env.pop("AUTOSKILLIT_HEADLESS", None)
    if skill_name is not None:
        env["AUTOSKILLIT_SKILL_NAME"] = skill_name
    else:
        env.pop("AUTOSKILLIT_SKILL_NAME", None)
    if session_type is not None:
        env["AUTOSKILLIT_SESSION_TYPE"] = session_type
    else:
        env.pop("AUTOSKILLIT_SESSION_TYPE", None)
    return env


def _build_payload(
    cmd: str,
    tmpdir: Path,
    *,
    use_bash_key: bool = False,
    include_execution_cwd: bool = True,
    payload_cwd: object = UNSET,
    tool_cwd: object = UNSET,
    tool_input_extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """A hook payload in the Bash or run_cmd shape.

    ``payload_cwd`` is the top-level session cwd; ``tool_cwd`` is run_cmd's own
    ``tool_input.cwd``. ``None`` omits a field, ``UNSET`` takes the default.
    """
    tool_input: dict[str, object] = {("command" if use_bash_key else "cmd"): cmd}
    if tool_cwd is not UNSET:
        if tool_cwd is not None:
            tool_input["cwd"] = str(tool_cwd)
    elif include_execution_cwd and not use_bash_key:
        tool_input["cwd"] = str(tmpdir)
    tool_input.update(tool_input_extra or {})
    payload: dict[str, object] = {
        "session_id": "guard-test-session",
        "tool_name": _BASH_TOOL_NAME if use_bash_key else _TOOL_NAME,
        "tool_input": tool_input,
    }
    if payload_cwd is not UNSET:
        if payload_cwd is not None:
            payload["cwd"] = str(payload_cwd)
    elif include_execution_cwd and use_bash_key:
        payload["cwd"] = str(tmpdir)
    return payload


def _run_guard(
    cmd: str,
    kitchen_open: bool,
    tmpdir,
    raw_stdin: str | None = None,
    skill_name: str | None = None,
    session_type: str | None = None,
    use_bash_key: bool = False,
    headless: bool = True,
    include_execution_cwd: bool = True,
    hook_config: dict[str, object] | None = None,
    *,
    extra_env: dict[str, str] | None = None,
    marker_root: Path | None = None,
    payload_cwd: object = UNSET,
    tool_cwd: object = UNSET,
    tool_input_extra: dict[str, object] | None = None,
    process_cwd: Path | None = None,
) -> str:
    """Invoke git_ops_guard.main() and return captured stdout.

    The kitchen marker is written under ``marker_root`` (default ``tmpdir``);
    ``process_cwd`` is the patched ``Path.cwd()`` the guard falls back to when
    the payload carries no cwd.
    """
    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        stdin_content = json.dumps(
            _build_payload(
                cmd,
                tmpdir,
                use_bash_key=use_bash_key,
                include_execution_cwd=include_execution_cwd,
                payload_cwd=payload_cwd,
                tool_cwd=tool_cwd,
                tool_input_extra=tool_input_extra,
            )
        )

    if kitchen_open:
        hook_cfg = (marker_root or tmpdir) / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(json.dumps(hook_config or {"kitchen": "open"}))

    clean_env = _make_clean_env(skill_name, session_type, headless=headless)
    clean_env.update(extra_env or {})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
            with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
                with unittest.mock.patch("pathlib.Path.cwd", return_value=process_cwd or tmpdir):
                    try:
                        git_ops_guard.main()
                    except SystemExit as exc:
                        assert exc.code == 0, f"Guard exited non-zero: {exc.code!r}"

    return buf.getvalue()


def _guard_exit_code(cmd: str, tmpdir: Path, **payload_kwargs: object) -> int | str | None:
    """Run git_ops_guard.main() in a headless skill session with the kitchen open
    and return the code it exits with.

    _run_guard requires a clean exit; this expects main() to terminate through
    SystemExit and leaves stdout and stderr to the caller's capsys.
    """
    hook_cfg = tmpdir / _HOOK_CONFIG_RELPATH
    hook_cfg.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg.write_text(json.dumps({"kitchen": "open"}))
    payload = _build_payload(cmd, tmpdir, **payload_kwargs)  # type: ignore[arg-type]
    with (
        unittest.mock.patch.dict(os.environ, _make_clean_env(None), clear=True),
        unittest.mock.patch("sys.stdin", io.StringIO(json.dumps(payload))),
        unittest.mock.patch("pathlib.Path.cwd", return_value=tmpdir),
        pytest.raises(SystemExit) as exit_info,
    ):
        git_ops_guard.main()
    return exit_info.value.code


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _checked_out_ref_result(output: str) -> dict[str, object]:
    data = json.loads(output)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    prefix = git_ops_guard.CHECKED_OUT_REF_DENY_PREFIX
    assert reason.startswith(prefix)
    return json.loads(reason.removeprefix(prefix))


def _guard_verdict(output: str) -> str:
    """Classify captured guard stdout as allow, checked-out-ref or other-deny."""
    if not output.strip():
        return "allow"
    assert _is_denied(output), output
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    if reason.startswith(git_ops_guard.CHECKED_OUT_REF_DENY_PREFIX):
        return "checked-out-ref"
    return "other-deny"


def _git(cwd: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    ).stdout.strip()


def build_linked_repo(tmp_path: Path, *, tracked_state_dir: bool = False) -> dict[str, Path | str]:
    """A primary on ``develop``, a linked worktree on ``review`` and two bystanders.

    ``unrelated`` is a separate repository on ``main``; ``bare`` is a bare
    repository. With ``tracked_state_dir`` the primary commits a
    ``.autoskillit/config.yaml`` before the linked worktree is created, so both
    worktrees carry their own ``.autoskillit/`` directory.
    """
    primary = init_checkout(tmp_path / "repo")
    _git(primary, "branch", "-m", "develop")
    commit_file(primary, "tracked.txt", "initial\n", "initial")
    if tracked_state_dir:
        commit_file(primary, ".autoskillit/config.yaml", "{}\n", "state dir")
    old_sha = head(primary)
    tree = _git(primary, "rev-parse", "HEAD^{tree}")
    new_sha = _git(primary, "commit-tree", tree, "-p", old_sha, input_text="next\n")
    linked = add_linked_worktree(primary, "review", start=old_sha)
    unrelated = init_checkout(tmp_path / "elsewhere")
    _git(unrelated, "branch", "-m", "main")
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "--bare", str(bare))
    return {
        "primary": primary,
        "linked": linked,
        "unrelated": unrelated,
        "bare": bare,
        "old_sha": old_sha,
        "new_sha": new_sha,
        "worktree_git_dir": _git(linked, "rev-parse", "--absolute-git-dir"),
        "common_git_dir": str((linked / _git(linked, "rev-parse", "--git-common-dir")).resolve()),
    }
