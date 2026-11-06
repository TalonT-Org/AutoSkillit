"""Cwd certainty of evaluated shell segments and the tokenizer facts it rests on.

Members: ``walk_evaluated_segments`` ``cwd_certain`` and ``verb_cwd`` rows, the
tokenizer's ``connector``, ``after_keyword`` and ``piped_from_previous`` fields
with the pipe-payload reach they keep.

Commands name the working directory as ``@w`` (the initial cwd), the existing
directories ``@a`` and ``@b``, and ``@missing``, which is never created. Each
command is bound to real absolute paths under ``tmp_path`` before it is walked.
The segment under test is always the one that runs ``git <subcommand>``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.hooks._classification._invocation_flags import _WRAPPER_SPECS
from autoskillit.hooks._classification._tokenizer import (
    _CommandSegment,
    _tokenize_command_segments_with_redirects,
)
from autoskillit.hooks._runtime._command_classification import all_evaluated_segments
from autoskillit.hooks._runtime._shell_segment_walk import (
    WalkedSegment,
    walk_evaluated_segments,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_PLACEHOLDER_RE = re.compile(r"@(w|a|b|missing)\b")


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, str]:
    for name in ("w", "a", "b"):
        (tmp_path / name).mkdir()
    return {name: str((tmp_path / name).resolve()) for name in ("w", "a", "b", "missing")}


def _bind(command: str, dirs: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda match: dirs[match.group(1)], command)


def _row_id(command: str, outcome: object) -> str:
    return f"{outcome}-{command.replace(chr(10), '<nl>')}"


def _git_segment(walk: list[WalkedSegment], subcommand: str) -> WalkedSegment:
    found = [
        seg
        for seg in walk
        if seg.start is not None and seg.executable[seg.start :][:2] == ["git", subcommand]
    ]
    assert len(found) == 1, f"expected one `git {subcommand}` segment, found {len(found)}"
    return found[0]


def _walk(command: str, dirs: dict[str, str], cwd: str) -> list[WalkedSegment]:
    walk = walk_evaluated_segments(_bind(command, dirs), cwd)
    assert walk is not None
    return walk


def _proven_rows(verb_cwd: str, *commands: str, subcommand: str = "x") -> list[object]:
    return [
        pytest.param(command, verb_cwd, subcommand, id=_row_id(command, verb_cwd))
        for command in commands
    ]


def _uncertain_rows(*commands: str, subcommand: str = "x") -> list[object]:
    return [pytest.param(command, subcommand, id=_row_id(command, False)) for command in commands]


_SPAWN_PROGRAM = 'import subprocess; subprocess.run(["git", "x"], cwd="@a")'

_CERTAIN_CASES = [
    # The Git segment runs only after a cd that certainly ran.
    *_proven_rows(
        "@a",
        "cd @a && git x",
        "cd @a; git x",
        "cd @a || exit 1; git x",
        "pushd @a >/dev/null && git x",
        "make && cd @a && git x",
        "cd @a &&\ngit x",
        "cd @a # go\ngit x",
    ),
    # A missing target still counts inside an unbroken && chain, which skips Git when the cd fails.
    *_proven_rows("@missing", "cd @missing && git x"),
    *_proven_rows("@a", "git status || true; cd @a && git checkout -b x", subcommand="checkout"),
    # A child shell started after a certain cd inherits it.
    *_proven_rows("@a", "cd @a && bash -c 'git x'; cd @b"),
    # A per-invocation override seeds its own certain cwd.
    *_proven_rows(
        "@a",
        "env -C @a git x",
        f"python3 -c '{_SPAWN_PROGRAM}'",
        "cd $X && env -C @a git x",
    ),
]

_UNCERTAIN_CASES = [
    # The cd may not have run, may have failed, or ran in another process.
    *_uncertain_rows(
        "cd @missing; git x",
        "true || cd @a; git x",
        "false && cd @a; git x",
        "true || cd @a && git x",
        "echo | cd @a && git x",
        "cd @a | git x",
        "cd @a & git x",
        "true ||\ncd @a\ngit x",
    ),
    # A cd inside a compound body or function body never certainly ran.
    *_uncertain_rows(
        "if false; then cd @a; fi; git x",
        "if false; then true; cd @a; fi; git x",
        "for d in a; do cd @a; done; git x",
        "false && { true; cd @a; }; git x",
        "f() { cd @a; }; git x",
        "case $v in a) cd @a;; esac; git x",
        "bash -c 'f() { cd @a; }; git x'",
    ),
    # Bare keyword lines leave no trace of the condition in the command text.
    *_uncertain_rows(
        "if\nfalse\nthen\ncd @a\nfi\ngit x",
        "while\ntrue\ndo\ncd @a\ndone\ngit x",
    ),
    # The connector resets at a group close.
    *_uncertain_rows("{ true; } || cd @a; git x"),
    # A redirect's child-scope records do not hide the pipe that follows.
    *_uncertain_rows('cd @a >"$(echo f)" | git x'),
    # Taint lasts for the scope, so an earlier pipeline over-denies.
    *_uncertain_rows("ls | head; cd @a; git x"),
    *_uncertain_rows("cd @a && bash -c 'false && cd @b; git x'"),
    # A dynamic or relative override does not stand alone.
    *_uncertain_rows("cd $X && env -C sub git x", 'env -C "$D" git x'),
]


@pytest.mark.parametrize(("command", "verb_cwd", "subcommand"), _CERTAIN_CASES)
def test_proven_git_segment_cwd_is_the_directory_the_command_names(
    dirs: dict[str, str], command: str, verb_cwd: str, subcommand: str
) -> None:
    segment = _git_segment(_walk(command, dirs, dirs["w"]), subcommand)
    assert segment.cwd_certain is True
    assert segment.verb_cwd == _bind(verb_cwd, dirs)


# verb_cwd is the shell's logical directory, so it is deliberately not asserted here.
@pytest.mark.parametrize(("command", "subcommand"), _UNCERTAIN_CASES)
def test_unproven_git_segment_cwd_is_uncertain(
    dirs: dict[str, str], command: str, subcommand: str
) -> None:
    segment = _git_segment(_walk(command, dirs, dirs["w"]), subcommand)
    assert segment.cwd_certain is False


@pytest.mark.parametrize(
    ("command", "certain", "verb_cwd"),
    [
        pytest.param("(cd @a); git x", True, "@w", id="subshell-cd-leaves-the-cwd"),
        pytest.param("pushd @a; popd; git x", False, "", id="popd-leaves-the-cwd-unknown"),
        pytest.param("cd $X && git x", False, "", id="dynamic-target"),
        pytest.param("cd - && git x", False, "", id="previous-directory"),
        pytest.param("builtin cd @a && git x", False, "@w", id="builtin-cd-is-unmodelled"),
        pytest.param("time cd @a; git x", True, "@a", id="time-runs-the-builtin-in-place"),
        pytest.param("time -p cd @a; git x", True, "@a", id="time-option-runs-the-builtin"),
        pytest.param("command cd @a; git x", False, "@a", id="command-runs-an-unmodelled-builtin"),
        pytest.param("exec cd @a; git x", True, "@w", id="exec-starts-a-process"),
        pytest.param("env cd @a; git x", True, "@w", id="env-starts-a-process"),
    ],
)
def test_git_segment_cwd_and_certainty_together(
    dirs: dict[str, str], command: str, certain: bool, verb_cwd: str
) -> None:
    segment = _git_segment(_walk(command, dirs, dirs["w"]), "x")
    assert segment.cwd_certain is certain
    assert segment.verb_cwd == _bind(verb_cwd, dirs)


@pytest.mark.parametrize("wrapper", sorted(_WRAPPER_SPECS))
def test_only_builtin_transparent_wrappers_let_a_cd_change_the_walk_directory(
    dirs: dict[str, str], wrapper: str
) -> None:
    segment = _git_segment(_walk(f"{wrapper} cd @a; git x", dirs, dirs["w"]), "x")
    expected = "@a" if wrapper in {"time", "command"} else "@w"
    assert segment.verb_cwd == _bind(expected, dirs)


def test_unknown_initial_cwd_is_never_certain(dirs: dict[str, str]) -> None:
    segment = _git_segment(_walk("git x", dirs, ""), "x")
    assert segment.verb_cwd == ""
    assert segment.cwd_certain is False


def _tokenize(command: str) -> list[_CommandSegment]:
    segments = _tokenize_command_segments_with_redirects(command)
    assert segments is not None
    return segments


def test_trailing_comment_does_not_join_the_next_line() -> None:
    command = "echo hi # c\ngit push origin main"
    segments = _tokenize(command)
    assert [segment.tokens for segment in segments] == [
        ["echo", "hi"],
        ["git", "push", "origin", "main"],
    ]
    assert segments[1].connector == ";"
    assert all_evaluated_segments(command) == [["echo", "hi"], ["git", "push", "origin", "main"]]


def test_newline_after_and_keeps_the_and_connector() -> None:
    segments = _tokenize("cd /a &&\ngit x")
    assert [segment.tokens for segment in segments] == [["cd", "/a"], ["git", "x"]]
    assert segments[1].connector == "&&"


def test_newline_after_pipe_keeps_the_pipe_and_its_payload() -> None:
    command = "echo 'git push' |\nbash"
    bash = _tokenize(command)[-1]
    assert bash.tokens == ["bash"]
    assert bash.piped_from_previous is True
    assert bash.connector == "|"
    assert ["git", "push"] in (all_evaluated_segments(command) or [])


@pytest.mark.parametrize(
    ("command", "connectors"),
    [
        pytest.param("a && b", ["", "&&"], id="and"),
        pytest.param("a || b", ["", "||"], id="or"),
        pytest.param("a | b", ["", "|"], id="pipe"),
        pytest.param("a & b", ["", "&"], id="background"),
        pytest.param("a ; b", ["", ";"], id="semicolon"),
        pytest.param("a\nb", ["", ";"], id="newline"),
        pytest.param("a && b\nc", ["", "&&", ";"], id="newline-after-a-complete-chain"),
        pytest.param("a &&\nb", ["", "&&"], id="newline-after-and"),
        pytest.param("a ||\n\nb", ["", "||"], id="blank-lines-after-or"),
        pytest.param("a |\nb", ["", "|"], id="newline-after-pipe"),
        pytest.param("a &\nb", ["", "&"], id="newline-after-background"),
        pytest.param("{ a; } || b", ["", "||"], id="group-close-clears-the-pending-connector"),
        pytest.param("a && { b; }", ["", "&&"], id="group-open-keeps-the-connector"),
        pytest.param("a && (b)", ["", "&&"], id="subshell-open-keeps-the-connector"),
        pytest.param("a ||\n{\nb\n}", ["", "||"], id="brace-group-on-its-own-lines"),
    ],
)
def test_connector_joining_each_segment(command: str, connectors: list[str]) -> None:
    assert [segment.connector for segment in _tokenize(command)] == connectors


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param(
            "if\nfalse\nthen\ncd /a\nfi\ngit x",
            [
                (["false"], True),
                (["cd", "/a"], True),
                (["fi"], False),
                (["git", "x"], False),
            ],
            id="if-then-on-bare-lines",
        ),
        pytest.param(
            "while\ntrue\ndo\ncd /a\ndone\ngit x",
            [
                (["true"], True),
                (["cd", "/a"], True),
                (["done"], False),
                (["git", "x"], False),
            ],
            id="while-do-on-bare-lines",
        ),
        pytest.param(
            "if a; then b; fi",
            [(["if", "a"], False), (["then", "b"], False), (["fi"], False)],
            id="keyword-with-a-command-is-not-dropped",
        ),
    ],
)
def test_dropped_bare_keyword_marks_the_next_segment(
    command: str, expected: list[tuple[list[str], bool]]
) -> None:
    segments = _tokenize(command)
    assert [(segment.tokens, segment.after_keyword) for segment in segments] == expected


def test_dropped_bare_keyword_keeps_the_pending_connector() -> None:
    segments = _tokenize("a &&\nif\nb")
    assert [(s.tokens, s.connector, s.after_keyword) for s in segments] == [
        (["a"], "", False),
        (["b"], "&&", True),
    ]
