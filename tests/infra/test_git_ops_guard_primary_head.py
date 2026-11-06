"""Cook primary-checkout HEAD rule of git_ops_guard, against real repositories.

Members: TestCookPrimaryHeadDenied, TestCookPrimaryHeadAllowed,
TestPrimaryHeadMarkerIndependence, TestPrimaryHeadAnchorFailure,
TestPrimaryHeadOtherSessions.

A cook session runs with a launch id, no headless flag and the project root as
its state root. Each row names the directory a payload starts in and a command;
a denial must carry ``PRIMARY_HEAD_DENY_PREFIX`` and an allowance must produce
no output at all. The guard denies before Git runs, so no row executes the
command it judges. ``<primary>``, ``<linked>``, ``<unrelated>``, ``<tmp>`` and
``<sha>`` in a command expand to the fixture repositories, the test directory
and a dangling commit.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import unittest.mock
from pathlib import Path

# The sys.path bootstrap for bare-name hook-sibling imports is centralized in
# tests/conftest.py.
import _git_target  # type: ignore[import-not-found]
import pytest

from autoskillit.hooks.guards import git_ops_guard
from tests._git_topology import commit_file, isolate_git_environment
from tests.infra._git_ops_guard_harness import (
    _HOOK_CONFIG_RELPATH,
    UNSET,
    _build_payload,
    _context,
    _git,
    _guard_verdict,
    _make_clean_env,
    _run_guard,
    build_linked_repo,
)

pytestmark = [
    pytest.mark.layer("infra"),
    pytest.mark.medium,
    pytest.mark.skipif(shutil.which("git") is None, reason="git binary required"),
]

_LAUNCH_ID = "0123456789abcdef"
_INCIDENT = "git checkout -b pr-issue-4726-step2 <sha>"
_UPDATE_DEVELOP = "git -C <primary> update-ref refs/heads/develop <sha>"

# name -> (headless, environment). Launched sessions also carry the state root.
_SESSIONS: dict[str, tuple[bool, dict[str, str]]] = {
    "cook": (False, {"AUTOSKILLIT_SESSION_TYPE": "skill", "AUTOSKILLIT_LAUNCH_ID": _LAUNCH_ID}),
    "cook-codex": (
        False,
        {
            "AUTOSKILLIT_HEADLESS": "",
            "AUTOSKILLIT_SESSION_TYPE": "skill",
            "AUTOSKILLIT_LAUNCH_ID": _LAUNCH_ID,
        },
    ),
    "order": (
        False,
        {"AUTOSKILLIT_SESSION_TYPE": "orchestrator", "AUTOSKILLIT_LAUNCH_ID": _LAUNCH_ID},
    ),
    "fleet": (
        False,
        {
            "AUTOSKILLIT_HEADLESS": "0",
            "AUTOSKILLIT_SESSION_TYPE": "fleet",
            "AUTOSKILLIT_LAUNCH_ID": _LAUNCH_ID,
        },
    ),
    "headless-skill": (
        True,
        {"AUTOSKILLIT_SESSION_TYPE": "skill", "AUTOSKILLIT_LAUNCH_ID": _LAUNCH_ID},
    ),
    "headless-orchestrator": (
        True,
        {"AUTOSKILLIT_SESSION_TYPE": "orchestrator", "AUTOSKILLIT_LAUNCH_ID": _LAUNCH_ID},
    ),
    "plain": (False, {}),
    "headless": (True, {}),
}
_UNLAUNCHED = ("plain", "headless")

# Commands that move HEAD of the primary checkout, with the rule's reason.
_DENIED_IN_PRIMARY = [
    ("git switch other", "moves_primary_head"),
    ("git checkout --detach", "moves_primary_head"),
    ("git checkout -", "moves_primary_head"),
    ("git switch y", "moves_primary_head"),
    ("git checkout -t origin/y", "moves_primary_head"),
    ("git checkout --orphan o", "moves_primary_head"),
    ("git symbolic-ref HEAD refs/heads/other", "moves_primary_head"),
    ("git branch -m renamed", "moves_primary_head"),
    ("git co -b x", "moves_primary_head"),
    ("git checkout other", "ambiguous_head_effect"),
    ("git checkout file.txt", "ambiguous_head_effect"),
    ("P=checkout; git $P -b x", "ambiguous_head_effect"),
]

# A shell whose working directory the guard cannot prove, or a command aimed at
# the primary from elsewhere.
_DENIED_BY_POSITION = [
    ("primary", "if false; then true; cd <linked>; fi; git checkout -b x", "unresolved_target"),
    ("primary", "true || cd <linked> && git checkout -b x", "unresolved_target"),
    ("linked", "GIT_DIR=<primary>/.git git checkout -b x", "unresolved_target"),
    ("linked", "bash -c 'cd <primary> && git checkout -b x'", "moves_primary_head"),
]

_ALLOWED_PATH_ONLY = [
    "git checkout -- file.txt",
    "git checkout other -- file.txt",
    "git checkout other file.txt",
    "git checkout .",
    "git checkout --ours file.txt",
    "git checkout -p",
    "git checkout -f",
    "git restore file.txt",
]
_ALLOWED_CURRENT_BRANCH_WORK = ["git commit -m x", "git merge feat"]

_ALLOWED_OUTSIDE_THE_PRIMARY = [
    ("primary", "cd <linked> && git checkout -b x"),
    ("primary", "git -C <linked> switch -c x"),
    ("primary", "git -C <linked> checkout --detach <sha>"),
    ("primary", "git -C <linked> switch --detach <sha>"),
    ("unrelated", "git checkout -b x"),
    ("primary", "git worktree add -b feat2 <tmp>/wt2"),
]


@pytest.fixture(autouse=True)
def _isolated_git_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_environment(monkeypatch, tmp_path / "home")


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, Path | str]:
    """The linked-worktree fixture plus a tracked file, two branches and ``origin/y``."""
    built = build_linked_repo(tmp_path, tracked_state_dir=True)
    primary = Path(str(built["primary"]))
    commit_file(primary, "file.txt", "a\n", "add file")
    _git(primary, "branch", "feat")
    _git(primary, "branch", "other")
    _git(primary, "remote", "add", "origin", str(built["bare"]))
    _git(primary, "push", "-q", "origin", "develop:refs/heads/y")
    _git(primary, "fetch", "-q", "origin")
    return built


def _expand(command: str, repo: dict[str, Path | str], tmp_path: Path) -> str:
    for name in ("primary", "linked", "unrelated"):
        command = command.replace(f"<{name}>", str(repo[name]))
    return command.replace("<tmp>", str(tmp_path)).replace("<sha>", str(repo["new_sha"]))


def _session_env(session: str, repo: dict[str, Path | str], *, state_root: bool) -> dict[str, str]:
    env = dict(_SESSIONS[session][1])
    if state_root and session not in _UNLAUNCHED:
        env["AUTOSKILLIT_STATE_ROOT"] = str(repo["primary"])
    return env


def _guard_output(
    repo: dict[str, Path | str],
    tmp_path: Path,
    command: str,
    *,
    cwd: str | Path = "primary",
    session: str = "cook",
    marker: bool = True,
    state_root: bool = True,
    run_cmd: bool = False,
    tool_cwd: object = UNSET,
) -> str:
    """The guard's stdout for *command* issued from the directory *cwd*.

    *cwd* is a fixture repository's name or a path. The kitchen marker, when
    present, sits only under the primary. A run_cmd payload carries the directory
    as its own ``cwd`` unless *tool_cwd* says otherwise.
    """
    path = cwd if isinstance(cwd, Path) else Path(str(repo[cwd]))
    return _run_guard(
        _expand(command, repo, tmp_path),
        kitchen_open=marker,
        tmpdir=tmp_path,
        marker_root=Path(str(repo["primary"])),
        headless=_SESSIONS[session][0],
        extra_env=_session_env(session, repo, state_root=state_root),
        use_bash_key=not run_cmd,
        payload_cwd=path,
        tool_cwd=(path if tool_cwd is UNSET else tool_cwd) if run_cmd else UNSET,
    )


def _verdict(output: str) -> str:
    """``allow``, ``primary-head``, ``checked-out-ref`` or ``other-deny``."""
    if output.strip():
        reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
        if reason.startswith(git_ops_guard.PRIMARY_HEAD_DENY_PREFIX):
            return "primary-head"
    return _guard_verdict(output)


def _primary_head_details(output: str) -> dict[str, str]:
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    prefix = git_ops_guard.PRIMARY_HEAD_DENY_PREFIX
    assert reason.startswith(prefix), reason
    details: dict[str, str] = json.loads(reason.removeprefix(prefix))
    return details


def _assert_verdict(output: str, expected: str, command: str) -> None:
    assert _verdict(output) == expected, _context(command, output)


def _current_head(primary: Path) -> str:
    return _git(primary, "symbolic-ref", "HEAD")


class TestCookPrimaryHeadDenied:
    """A cook session may not move HEAD of the checkout that holds its project state."""

    def test_denies_the_incident_and_leaves_the_primary_on_its_branch(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        primary = Path(str(repo["primary"]))
        output = _guard_output(repo, tmp_path, _INCIDENT)

        _assert_verdict(output, "primary-head", _INCIDENT)
        assert _current_head(primary) == "refs/heads/develop", _context(_INCIDENT)
        details = _primary_head_details(output)
        context = _context(_INCIDENT, details)
        assert details["reason"] == "moves_primary_head", context
        assert details["subcommand"] == "checkout", context
        assert details["head_effect"] == "moves", context
        assert details["protected_primary"] == str(primary), context
        assert details["execution_cwd"] == str(primary), context
        assert details["session_id"] == "guard-test-session", context
        assert "git -C <worktree>" in details["remedy"], context
        assert "cd <worktree>" in details["remedy"], context

    @pytest.mark.parametrize("session", ["cook", "cook-codex"])
    @pytest.mark.parametrize("run_cmd", [False, True], ids=["bash", "run_cmd"])
    def test_denies_the_incident_in_every_payload_shape(
        self, repo: dict[str, Path | str], tmp_path: Path, run_cmd: bool, session: str
    ) -> None:
        output = _guard_output(repo, tmp_path, _INCIDENT, run_cmd=run_cmd, session=session)
        _assert_verdict(output, "primary-head", _INCIDENT)

    @pytest.mark.parametrize(("command", "reason"), _DENIED_IN_PRIMARY)
    def test_denies_each_kind_of_head_move_in_the_primary(
        self, repo: dict[str, Path | str], tmp_path: Path, command: str, reason: str
    ) -> None:
        _git(Path(str(repo["primary"])), "config", "alias.co", "checkout")
        output = _guard_output(repo, tmp_path, command)

        _assert_verdict(output, "primary-head", command)
        assert _primary_head_details(output)["reason"] == reason, _context(command, output)

    @pytest.mark.parametrize("command", ["git checkout other", "git checkout file.txt"])
    def test_names_the_unambiguous_spellings_for_an_ambiguous_checkout(
        self, repo: dict[str, Path | str], tmp_path: Path, command: str
    ) -> None:
        remedy = _primary_head_details(_guard_output(repo, tmp_path, command))["remedy"]

        for spelling in (
            "git checkout -- <path>",
            "git restore <path>",
            "git -C <worktree> switch <branch>",
            "git -C <worktree> switch --detach <commit>",
        ):
            assert spelling in remedy, _context(command, remedy)

    def test_denies_a_clone_made_and_used_in_the_same_command(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        """The clone does not exist yet, so ``d`` is judged by its nearest existing
        ancestor: the primary.
        """
        command = "git clone <primary> d && git -C d checkout -b f"
        _assert_verdict(_guard_output(repo, tmp_path, command), "primary-head", command)

    @pytest.mark.parametrize(("cwd", "command", "reason"), _DENIED_BY_POSITION)
    def test_denies_a_command_whose_position_leads_to_the_primary_or_cannot_be_proven(
        self, repo: dict[str, Path | str], tmp_path: Path, cwd: str, command: str, reason: str
    ) -> None:
        output = _guard_output(repo, tmp_path, command, cwd=cwd)

        _assert_verdict(output, "primary-head", command)
        assert _primary_head_details(output)["reason"] == reason, _context(command, output)

    @pytest.mark.parametrize("tool_cwd", [".", ""])
    def test_denies_run_cmd_whose_tool_cwd_is_unknown(
        self, repo: dict[str, Path | str], tmp_path: Path, tool_cwd: str
    ) -> None:
        command = "git checkout -b x"
        output = _guard_output(repo, tmp_path, command, run_cmd=True, tool_cwd=tool_cwd)

        _assert_verdict(output, "primary-head", command)
        assert _primary_head_details(output)["reason"] == "unresolved_target", _context(
            command, output
        )


class TestCookPrimaryHeadAllowed:
    @pytest.mark.parametrize("command", _ALLOWED_PATH_ONLY)
    def test_allows_a_command_that_only_restores_paths(
        self, repo: dict[str, Path | str], tmp_path: Path, command: str
    ) -> None:
        _assert_verdict(_guard_output(repo, tmp_path, command), "allow", command)

    @pytest.mark.parametrize("command", _ALLOWED_CURRENT_BRANCH_WORK)
    def test_allows_work_on_the_current_branch(
        self, repo: dict[str, Path | str], tmp_path: Path, command: str
    ) -> None:
        _assert_verdict(_guard_output(repo, tmp_path, command), "allow", command)

    @pytest.mark.parametrize(("cwd", "command"), _ALLOWED_OUTSIDE_THE_PRIMARY)
    def test_allows_a_head_move_in_a_linked_worktree_or_another_repository(
        self, repo: dict[str, Path | str], tmp_path: Path, cwd: str, command: str
    ) -> None:
        _assert_verdict(_guard_output(repo, tmp_path, command, cwd=cwd), "allow", command)

    def test_allows_work_in_a_clone_made_by_an_earlier_command(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        clone = "<primary>/.autoskillit/temp/c"
        make = f"git clone <primary> {clone}"
        use = f"git -C {clone} checkout -b f"

        _assert_verdict(_guard_output(repo, tmp_path, make), "allow", make)
        _git(tmp_path, "clone", "-q", str(repo["primary"]), _expand(clone, repo, tmp_path))
        _assert_verdict(_guard_output(repo, tmp_path, use), "allow", use)


class TestPrimaryHeadMarkerIndependence:
    """The rule follows the session, not the kitchen marker or the payload's directory."""

    def test_protects_the_anchor_primary_from_a_payload_in_a_worktree_with_its_own_state(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        move = "git -C <primary> checkout -b x"
        output = _guard_output(repo, tmp_path, move, cwd="linked", marker=False, state_root=False)
        _assert_verdict(output, "primary-head", move)

    def test_still_protects_a_checked_out_ref_with_the_anchor_drifted_to_a_worktree(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        output = _guard_output(
            repo, tmp_path, _UPDATE_DEVELOP, cwd="linked", marker=False, state_root=False
        )
        _assert_verdict(output, "checked-out-ref", _UPDATE_DEVELOP)

    def test_denies_the_incident_with_no_marker_anywhere(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        _assert_verdict(
            _guard_output(repo, tmp_path, _INCIDENT, marker=False), "primary-head", _INCIDENT
        )

    def test_order_session_without_a_marker_still_protects_a_checked_out_ref(
        self, repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        output = _guard_output(repo, tmp_path, _UPDATE_DEVELOP, session="order", marker=False)
        _assert_verdict(output, "checked-out-ref", _UPDATE_DEVELOP)


def _failing_probe(monkeypatch: pytest.MonkeyPatch, repository: Path) -> list[tuple[str, ...]]:
    """Make ``rev-parse`` fail with a dubious-ownership error for *repository* itself.

    Returns the queries that reached the failure.
    """
    real = _git_target._git_result
    guarded = os.path.realpath(repository)
    reached: list[tuple[str, ...]] = []

    def probe(cwd: str, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args[:1] == ("rev-parse",) and os.path.realpath(cwd) == guarded:
            reached.append(args)
            message = f"fatal: detected dubious ownership in repository at '{cwd}'\n"
            return subprocess.CompletedProcess(
                ["git", *args], 128, stdout=b"", stderr=message.encode()
            )
        return real(cwd, *args)

    monkeypatch.setattr(_git_target, "_git_result", probe)
    return reached


def _cook_exit_code(
    command: str, tmp_path: Path, repo: dict[str, Path | str], payload_cwd: Path
) -> int | str | None:
    """Run main() in a cook session with the kitchen open and return its exit code.

    ``_run_guard`` requires a clean exit, which a blocked classification is not.
    """
    primary = Path(str(repo["primary"]))
    marker = primary / _HOOK_CONFIG_RELPATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"kitchen": "open"}))
    payload = _build_payload(
        _expand(command, repo, tmp_path), tmp_path, use_bash_key=True, payload_cwd=payload_cwd
    )
    env = {**_make_clean_env(None, headless=False), **_session_env("cook", repo, state_root=True)}
    with (
        unittest.mock.patch.dict(os.environ, env, clear=True),
        unittest.mock.patch("sys.stdin", io.StringIO(json.dumps(payload))),
        unittest.mock.patch("pathlib.Path.cwd", return_value=tmp_path),
        pytest.raises(SystemExit) as exit_info,
    ):
        git_ops_guard.main()
    return exit_info.value.code


class TestPrimaryHeadAnchorFailure:
    """An anchor repository Git refuses to query fails only the command that needs it."""

    def test_allows_commands_that_never_ask_for_the_anchor(
        self, repo: dict[str, Path | str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        primary = Path(str(repo["primary"]))
        reached = _failing_probe(monkeypatch, primary)
        for command in ("ls", "git status"):
            _assert_verdict(_guard_output(repo, tmp_path, command), "allow", command)
        reached.clear()

        _assert_verdict(
            _guard_output(repo, tmp_path, "git status", cwd=primary / ".autoskillit"),
            "allow",
            "git status",
        )
        assert reached == [], _context("git status asked for the anchor", reached)

    def test_allows_a_head_move_in_a_linked_worktree(
        self, repo: dict[str, Path | str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _failing_probe(monkeypatch, Path(str(repo["primary"])))
        command = "git checkout -b x"
        _assert_verdict(_guard_output(repo, tmp_path, command, cwd="linked"), "allow", command)

    def test_blocks_the_incident_when_the_anchor_cannot_be_resolved(
        self,
        repo: dict[str, Path | str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        primary = Path(str(repo["primary"]))
        reached = _failing_probe(monkeypatch, primary)

        code = _cook_exit_code(_INCIDENT, tmp_path, repo, primary / ".autoskillit")

        captured = capsys.readouterr()
        context = _context(_INCIDENT, captured)
        assert code == 2, context
        assert captured.out == "", context
        assert "git_ops_guard: RuntimeError: cook anchor repository unresolved" in captured.err, (
            context
        )
        assert reached, context


class TestPrimaryHeadOtherSessions:
    """Only a launched cook session is held to the rule; other shapes are unchanged."""

    @pytest.mark.parametrize("command", [_INCIDENT, "git switch other"])
    @pytest.mark.parametrize(
        "session", ["plain", "order", "fleet", "headless-skill", "headless-orchestrator"]
    )
    def test_allows_a_head_move_in_the_primary(
        self, repo: dict[str, Path | str], tmp_path: Path, session: str, command: str
    ) -> None:
        output = _guard_output(repo, tmp_path, command, session=session)
        _assert_verdict(output, "allow", f"{session}: {command}")

    @pytest.mark.parametrize("session", ["plain", "headless"])
    def test_leaves_a_checked_out_ref_alone_while_the_kitchen_is_closed(
        self, repo: dict[str, Path | str], tmp_path: Path, session: str
    ) -> None:
        output = _guard_output(repo, tmp_path, _UPDATE_DEVELOP, session=session, marker=False)
        _assert_verdict(output, "allow", f"{session}: {_UPDATE_DEVELOP}")
