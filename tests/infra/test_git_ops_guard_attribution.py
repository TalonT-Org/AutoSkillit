"""Effective-target attribution matrix for git_ops_guard's checked-out-ref preflight.

Members: TestEffectiveTargetAttribution.

Each row names the directory a Bash payload starts in and a command whose effective
Git target differs from that directory, or whose target the guard cannot attribute.
A denial must carry ``CHECKED_OUT_REF_DENY_PREFIX``; an allowance must produce no
output at all. ``<primary>``, ``<linked>``, ``<unrelated>`` and ``<sha>`` in a command
expand to the fixture repositories and a dangling commit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.infra._git_ops_guard_harness import (
    _git,
    _guard_verdict,
    _run_guard,
    build_linked_repo,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_M1 = "git update-ref refs/heads/develop <sha>"
_M2 = "git checkout -Bdevelop <sha>"

_SESSION_SHAPES = (
    (True, "skill"),
    (True, "orchestrator"),
    (False, "orchestrator"),
    (False, "skill"),
)

_PYTHON_UNRESOLVED_SUBPROCESS = (
    "python3 - <<'EOF'\n"
    "import subprocess\n"
    'cmd = "git update-ref refs/heads/develop <sha>".split()\n'
    "subprocess.run(cmd)\n"
    "EOF"
)
_PYTHON_VARIABLE_PATH_WRITE = (
    "python3 - <<'EOF'\n"
    "import pathlib\n"
    'path = pathlib.Path(".autoskillit/temp") / "x.md"\n'
    'path.write_text("x")\n'
    "EOF"
)
_PYTHON_VARIABLE_PATH_WRITE_MENTIONING_GIT = _PYTHON_VARIABLE_PATH_WRITE.replace(
    "import pathlib\n", "import pathlib\n# git-tracked state stays untouched\n"
)

_DENIED_FROM_CWD = [
    # Layout overrides and -C/env -C retarget the command away from the payload cwd.
    ("unrelated", f"GIT_DIR=<primary>/.git {_M1}"),
    ("unrelated", f"export GIT_DIR=<primary>/.git; {_M1}"),
    ("unrelated", "git --git-dir=<primary>/.git update-ref refs/heads/develop <sha>"),
    ("unrelated", "git --bare update-ref refs/heads/develop <sha>"),
    ("unrelated", f"env -C <primary> {_M1}"),
    # The shell's working directory at the Git segment is unknown or differs from the payload's.
    ("unrelated", f"cd <primary> && cd /nonexistent; {_M1}"),
    ("unrelated", f"P=<primary>; cd $P && {_M1}"),
    ("unrelated", f"pushd <primary>; popd; {_M1}"),
    ("unrelated", f"cd <unrelated> && bash -c 'cd <primary> && {_M1}'"),
    ("primary", f"bash -c '{_M1}'; cd <unrelated>"),
    ("primary", f"false && cd <unrelated>; {_M1}"),
    ("primary", f"if false; then true; cd <unrelated>; fi; {_M1}"),
    ("primary", f"true || cd <unrelated> && {_M1}"),
    ("primary", f"cd <unrelated> | {_M1}"),
    ("primary-git", _M1),
    # An unknown command identity is ambiguous in any repository.
    ("unrelated", "P=update-ref; git $P refs/heads/x <sha>"),
    ("linked", "P=update-ref; git $P refs/heads/develop <sha>"),
    ("linked", "G=git; $G update-ref refs/heads/develop <sha>"),
    ("linked", "$(git --exec-path)/git-update-ref refs/heads/develop <sha>"),
    ("linked", "git --frobnicate -C <primary> update-ref refs/heads/develop <sha>"),
    # Quotes and backslashes inside the command word do not hide it from the prefilter.
    ("linked", "g\\it checkout -Bdevelop <sha>"),
    ("linked", 'gi""t branch -f develop <sha>'),
    ("linked", "'g'it switch -Cdevelop"),
    # Aliases the command word reaches through the command line.
    ("linked", "git -c alias.ur=update-ref ur refs/heads/develop <sha>"),
    ("linked", "git config alias.ur update-ref && git ur refs/heads/develop <sha>"),
    (
        "linked",
        "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.ur GIT_CONFIG_VALUE_0=update-ref; "
        "git ur refs/heads/develop <sha>",
    ),
    # Raw writes into ref storage, judged against the repository the path belongs to.
    ("unrelated", "rm <primary>/.git/refs/heads/develop"),
    ("linked", "sed -ni s/a/b/ <primary>/.git/refs/heads/develop"),
    (
        "primary",
        "python3 -c 'import os; "
        """open(os.path.join(os.environ["G"], "refs/heads/develop"), "w")'""",
    ),
    (
        "primary",
        "python3 -c 'import os,pathlib; "
        """pathlib.Path(os.environ["G"], "refs/heads/develop").write_text("x")'""",
    ),
    ("primary", 'X=.git/refs/heads/develop; rm "$X"'),
    ("primary", 'rm -rf "$GIT_DIR"'),
    ("primary", "rm -rf $GIT_COMMON_DIR"),
    ("primary", 'rm -rf "${GIT_WORK_TREE}"'),
    ("primary", _PYTHON_UNRESOLVED_SUBPROCESS),
]

_ALLOWED_FROM_CWD = [
    # A cd to a variable leaves the working directory unknown; none of these
    # commands rewrites a ref.
    ("unrelated", "cd $X && git branch -a"),
    ("unrelated", "cd $X && git checkout -b f"),
    ("unrelated", "cd $X && git lfs pull"),
    ("unrelated", 'cd "$X" && git push -u origin HEAD'),
    ("unrelated", 'cd "$X" && git fetch origin'),
    # A subshell cd does not move the parent shell, and develop is not checked out
    # in the unrelated repository.
    ("unrelated", "(cd <primary>); git update-ref refs/heads/develop <sha>"),
    ("unrelated", "git clone <primary> d && cd d && git checkout -b f"),
    # A bare repository checks no ref out; read-only commands may retarget the primary.
    ("bare", "git symbolic-ref HEAD refs/heads/x"),
    ("unrelated", "git -C <primary> status"),
    ("unrelated", "GIT_DIR=<primary>/.git git log"),
    ("unrelated", "git --no-optional-locks status"),
    ("unrelated", "git --list-cmds=main"),
    # Writes through variables that name no ref storage.
    ("primary", 'rm "$TMPFILE"'),
    ("primary", 'cp a "$DEST"'),
    ("primary", 'cd "$X" && echo x > out.txt'),
    ("primary", _PYTHON_VARIABLE_PATH_WRITE),
    ("primary", """python3 -c 'import os; open(os.environ["P"],"w").write("x")'"""),
    # The same writes with a `git` word in the text pass the prefilter, so the
    # ref-storage rule alone must keep them allowed.
    ("primary", _PYTHON_VARIABLE_PATH_WRITE_MENTIONING_GIT),
    ("primary", """python3 -c 'import os; open(os.environ["P"],"w").write("x")  # git'"""),
]

# Commands the prefilter lets through without reaching the Git analysis.
_BENIGN_COMMANDS = [
    "ls",
    "cat README",
    "uv run pytest -q",
    "echo hi > /tmp/x",
    "python3 -c 'print(1)'",
    "make build",
    "echo legitimate digit",
    "curl https://gitlab.example/api",
    "cat .gitignore",
    "gitk --all",
    'echo "$preset"',
    'X=$branching; echo "$X"',
]

_RUN_CMD_UNKNOWN_CWD_DENIED = [_M1, _M2, "git push . HEAD:develop", "git-receive-pack x"]

_RUN_CMD_UNKNOWN_CWD_ALLOWED = [
    "cd <unrelated> && git update-ref refs/heads/x <sha>",
    "git branch -a",
    "git checkout -b fresh",
    "git status",
]


def _expand(command: str, repo: dict[str, Path | str]) -> str:
    for name in ("primary", "linked", "unrelated"):
        command = command.replace(f"<{name}>", str(repo[name]))
    return command.replace("<sha>", str(repo["new_sha"]))


def _payload_dir(repo: dict[str, Path | str], name: str) -> Path:
    if name == "primary-git":
        return Path(str(repo["primary"])) / ".git"
    return Path(str(repo[name]))


def _bash_verdict(
    repo: dict[str, Path | str],
    tmp_path: Path,
    cwd: str,
    command: str,
    *,
    headless: bool = True,
    session_type: str | None = None,
    extra_env: dict[str, str] | None = None,
    tool_input_extra: dict[str, object] | None = None,
) -> str:
    """The guard's verdict for a Bash payload whose top-level cwd is ``cwd``.

    The kitchen marker sits at ``tmp_path``, the common ancestor every payload
    directory resolves its state root to.
    """
    output = _run_guard(
        _expand(command, repo),
        kitchen_open=True,
        tmpdir=tmp_path,
        use_bash_key=True,
        payload_cwd=_payload_dir(repo, cwd),
        headless=headless,
        session_type=session_type,
        extra_env=extra_env,
        tool_input_extra=tool_input_extra,
    )
    return _guard_verdict(output)


class TestEffectiveTargetAttribution:
    """A command is judged by the repository and ref it reaches, whatever directory
    the payload starts in; a target the guard cannot attribute is denied only when
    the command can mutate a ref.
    """

    @pytest.fixture(autouse=True)
    def _no_ambient_git_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in [name for name in os.environ if name.startswith("GIT_")]:
            monkeypatch.delenv(name)

    @pytest.fixture
    def linked_repo(self, tmp_path: Path) -> dict[str, Path | str]:
        return build_linked_repo(tmp_path, tracked_state_dir=False)

    @pytest.mark.parametrize(("cwd", "command"), _DENIED_FROM_CWD)
    def test_denies_command_reaching_a_checked_out_ref(
        self, linked_repo: dict[str, Path | str], tmp_path: Path, cwd: str, command: str
    ) -> None:
        assert _bash_verdict(linked_repo, tmp_path, cwd, command) == "checked-out-ref"

    def test_denies_dynamic_command_word_resolved_by_hook_env(
        self, linked_repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        verdict = _bash_verdict(
            linked_repo, tmp_path, "linked", "$G checkout -Bdevelop <sha>", extra_env={"G": "git"}
        )
        assert verdict == "checked-out-ref"

    def test_denies_alias_configured_in_the_repository(
        self, linked_repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        _git(Path(str(linked_repo["primary"])), "config", "alias.ur", "update-ref")
        verdict = _bash_verdict(linked_repo, tmp_path, "linked", "git ur refs/heads/develop <sha>")
        assert verdict == "checked-out-ref"

    @pytest.mark.parametrize("tool_cwd", [".", ""])
    @pytest.mark.parametrize("command", _RUN_CMD_UNKNOWN_CWD_DENIED)
    def test_denies_run_cmd_with_unknown_tool_cwd(
        self, linked_repo: dict[str, Path | str], tmp_path: Path, command: str, tool_cwd: str
    ) -> None:
        output = _run_guard(
            _expand(command, linked_repo), kitchen_open=True, tmpdir=tmp_path, tool_cwd=tool_cwd
        )
        assert _guard_verdict(output) == "checked-out-ref"

    def test_denies_bash_whose_workdir_names_the_linked_worktree(
        self, linked_repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        verdict = _bash_verdict(
            linked_repo,
            tmp_path,
            "unrelated",
            _M1,
            tool_input_extra={"workdir": str(linked_repo["linked"])},
        )
        assert verdict == "checked-out-ref"

    @pytest.mark.parametrize(("cwd", "command"), _ALLOWED_FROM_CWD)
    def test_allows_command_that_cannot_reach_a_checked_out_ref(
        self, linked_repo: dict[str, Path | str], tmp_path: Path, cwd: str, command: str
    ) -> None:
        assert _bash_verdict(linked_repo, tmp_path, cwd, command) == "allow"

    @pytest.mark.parametrize("command", _RUN_CMD_UNKNOWN_CWD_ALLOWED)
    def test_allows_run_cmd_with_empty_tool_cwd_when_no_ref_is_threatened(
        self, linked_repo: dict[str, Path | str], tmp_path: Path, command: str
    ) -> None:
        output = _run_guard(
            _expand(command, linked_repo), kitchen_open=True, tmpdir=tmp_path, tool_cwd=""
        )
        assert _guard_verdict(output) == "allow"

    @pytest.mark.parametrize(("headless", "session_type"), _SESSION_SHAPES)
    @pytest.mark.parametrize("command", _BENIGN_COMMANDS)
    def test_allows_commands_that_exit_at_the_prefilter_in_every_session_shape(
        self,
        linked_repo: dict[str, Path | str],
        tmp_path: Path,
        command: str,
        headless: bool,
        session_type: str,
    ) -> None:
        verdict = _bash_verdict(
            linked_repo, tmp_path, "primary", command, headless=headless, session_type=session_type
        )
        assert verdict == "allow"

    @pytest.mark.parametrize(
        ("cwd", "command", "verdict"),
        [
            ("unrelated", f"GIT_DIR=<primary>/.git {_M1}", "checked-out-ref"),
            ("primary", f"bash -c '{_M1}'; cd <unrelated>", "checked-out-ref"),
            ("unrelated", "cd $X && git branch -a", "allow"),
        ],
    )
    def test_verdict_is_the_same_in_every_session_shape(
        self,
        linked_repo: dict[str, Path | str],
        tmp_path: Path,
        cwd: str,
        command: str,
        verdict: str,
    ) -> None:
        verdicts = {
            shape: _bash_verdict(
                linked_repo, tmp_path, cwd, command, headless=shape[0], session_type=shape[1]
            )
            for shape in _SESSION_SHAPES
        }
        assert verdicts == dict.fromkeys(_SESSION_SHAPES, verdict)
