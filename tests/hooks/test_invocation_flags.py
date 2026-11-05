"""Invocation grammar of hooks/_classification/_invocation_flags.py.

Members: shell_c_payload and python_c_program matrices (each row is the argv after
the interpreter word, written as space-separated shorthand where ``{P}`` stands for
a program that itself contains spaces), the ``_WRAPPER_SPECS`` table, the
``_wrapper_command_start``, ``_wrapper_chdir_value``, ``_env_splits_string`` and
``_launcher_position_unresolved`` rows.

Every value is a ``(value, unresolved)`` pair: *unresolved* is True when the
command a launcher runs cannot be located, which is reported out of band so shared
consumers keep their verb.
"""

from __future__ import annotations

import pytest

from autoskillit.hooks._classification._invocation_flags import (
    _PYTHON_INVOCATION_BOOLEAN_FLAGS,
    _PYTHON_INVOCATION_FLAG_SPEC,
    _SHELL_INVOCATION_BOOLEAN_FLAGS,
    _SHELL_INVOCATION_FLAG_SPEC,
    _WRAPPER_SPECS,
    _env_splits_string,
    _launcher_position_unresolved,
    _wrapper_chdir_value,
    _wrapper_command_start,
    python_c_program,
    shell_c_payload,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_PROGRAM = "git update-ref refs/heads/x y"


def _argv(shorthand: str) -> list[str]:
    return [word.replace("{P}", _PROGRAM) for word in shorthand.split()]


_SHELL_PAYLOAD_FOUND = [
    "-c {P}",
    "-lc {P}",
    "-xc {P}",
    "-ec {P}",
    "-e -c {P}",
    "--norc -c {P}",
    "--noprofile -c {P}",
    "--login -c {P}",
    "-O extglob -c {P}",
    "+O extglob -c {P}",
    "-o pipefail -c {P}",
    "+o history -c {P}",
    "--rcfile rc -c {P}",
    "--init-file rc -c {P}",
    "-c -- {P}",
    "-D -c {P}",
    "-c {P} name arg",
]
_SHELL_PAYLOAD_UNRESOLVED = [
    "--bogus -c {P}",
    "--bogus",
    "-c",
    "-lc",
    "-O extglob -c",
]
_SHELL_PAYLOAD_ABSENT = [
    "",
    "script.sh",
    "-s",
    "-x script.sh",
    "script.sh -c x",
    "-",
    "--version",
    "--help",
]


@pytest.mark.parametrize("shorthand", _SHELL_PAYLOAD_FOUND)
def test_shell_c_payload_finds_the_program(shorthand: str) -> None:
    assert shell_c_payload(_argv(shorthand)) == (_PROGRAM, False)


@pytest.mark.parametrize("shorthand", _SHELL_PAYLOAD_UNRESOLVED)
def test_shell_c_payload_without_a_locatable_program_is_unresolved(shorthand: str) -> None:
    assert shell_c_payload(_argv(shorthand)) == (None, True)


@pytest.mark.parametrize("shorthand", _SHELL_PAYLOAD_ABSENT)
def test_shell_c_payload_without_c_mode_has_no_program(shorthand: str) -> None:
    assert shell_c_payload(_argv(shorthand)) == (None, False)


@pytest.mark.parametrize("flag", sorted(_SHELL_INVOCATION_BOOLEAN_FLAGS))
def test_shell_long_boolean_flag_takes_no_value(flag: str) -> None:
    assert shell_c_payload([flag, "-c", _PROGRAM]) == (_PROGRAM, False)


@pytest.mark.parametrize("flag", sorted(set(_SHELL_INVOCATION_FLAG_SPEC) - {"-c"}))
def test_shell_value_flag_takes_one_value(flag: str) -> None:
    assert shell_c_payload([flag, "VALUE", "-c", _PROGRAM]) == (_PROGRAM, False)


_PYTHON_PROGRAM_FOUND = [
    "-c {P}",
    "-Bc {P}",
    "-Ic {P}",
    "-c{P}",
    "-B -c {P}",
    "-I -c {P}",
    "-R -c {P}",
    "-t -c {P}",
    "-W error -c {P}",
    "-Werror -c {P}",
    "-X dev -c {P}",
    "-Xdev -c {P}",
    "-W ignore -X dev -Bc {P}",
    "--check-hash-based-pycs always -c {P}",
    "-c {P} -m x",
]
_PYTHON_PROGRAM_UNRESOLVED = [
    "--bogus -c {P}",
    "--bogus",
    "-c",
    "-Bc",
]
_PYTHON_PROGRAM_ABSENT = [
    "",
    "-m pip",
    "-Bm pip",
    "-B -m pip -c x",
    "script.py",
    "script.py -c x",
    "- -c x",
    "-- -c x",
    "--version",
    "--help",
]


@pytest.mark.parametrize("shorthand", _PYTHON_PROGRAM_FOUND)
def test_python_c_program_finds_the_program(shorthand: str) -> None:
    assert python_c_program(_argv(shorthand)) == (_PROGRAM, False)


@pytest.mark.parametrize("shorthand", _PYTHON_PROGRAM_UNRESOLVED)
def test_python_c_program_without_a_locatable_program_is_unresolved(shorthand: str) -> None:
    assert python_c_program(_argv(shorthand)) == (None, True)


@pytest.mark.parametrize("shorthand", _PYTHON_PROGRAM_ABSENT)
def test_python_c_program_without_c_has_no_program(shorthand: str) -> None:
    assert python_c_program(_argv(shorthand)) == (None, False)


@pytest.mark.parametrize("flag", sorted(_PYTHON_INVOCATION_BOOLEAN_FLAGS))
def test_python_long_boolean_flag_takes_no_value(flag: str) -> None:
    assert python_c_program([flag, "-c", _PROGRAM]) == (_PROGRAM, False)


@pytest.mark.parametrize("flag", sorted(set(_PYTHON_INVOCATION_FLAG_SPEC) - {"-c", "-m"}))
def test_python_value_flag_takes_one_value(flag: str) -> None:
    assert python_c_program([flag, "VALUE", "-c", _PROGRAM]) == (_PROGRAM, False)


def test_boolean_flag_sets_do_not_overlap_the_value_specs() -> None:
    assert not _SHELL_INVOCATION_BOOLEAN_FLAGS & set(_SHELL_INVOCATION_FLAG_SPEC)
    assert not _PYTHON_INVOCATION_BOOLEAN_FLAGS & set(_PYTHON_INVOCATION_FLAG_SPEC)


def test_wrapper_specs_model_exactly_the_option_bearing_wrappers() -> None:
    assert set(_WRAPPER_SPECS) == {
        "command",
        "exec",
        "nice",
        "nohup",
        "stdbuf",
        "sudo",
        "time",
        "timeout",
    }


def test_wrapper_specs_option_spellings_are_flags_without_overlap() -> None:
    for name, spec in _WRAPPER_SPECS.items():
        spellings = [*spec.booleans, *spec.values, *spec.chdir]
        assert all(spelling.startswith("-") for spelling in spellings), name
        assert len(spellings) == len(set(spellings)), name


def test_sudo_host_option_stays_unmodelled() -> None:
    spec = _WRAPPER_SPECS["sudo"]
    assert not {"-h", "--host"} & (spec.booleans | spec.values | spec.chdir)


def test_only_sudo_declares_a_chdir_option_and_only_timeout_a_leading_operand() -> None:
    assert {name for name, spec in _WRAPPER_SPECS.items() if spec.chdir} == {"sudo"}
    assert _WRAPPER_SPECS["sudo"].chdir == {"-D", "--chdir"}
    assert {name for name, spec in _WRAPPER_SPECS.items() if spec.leading_operands} == {"timeout"}
    assert _WRAPPER_SPECS["timeout"].leading_operands == 1


_RESOLVED_COMMAND_START = [
    ("timeout 10 git s", 2),
    ("timeout -k 5 10 git s", 4),
    ("timeout --kill-after=5 10 git s", 3),
    ("timeout -s KILL 10 git s", 4),
    ("timeout --signal=KILL 10 git s", 3),
    ("timeout --foreground --preserve-status -v 10 git s", 5),
    ("timeout -- 10 git s", 3),
    ("sudo git s", 1),
    ("sudo -n git s", 2),
    ("sudo -i git s", 2),
    ("sudo -u root git s", 3),
    ("sudo -uroot git s", 2),
    ("sudo --user=root git s", 2),
    ("sudo --user root git s", 3),
    ("sudo --host=h git s", 2),
    ("sudo -D /x git s", 3),
    ("sudo --chdir=/x git s", 2),
    ("sudo -E -- git s", 3),
    ("sudo -- git s", 2),
    ("nice git s", 1),
    ("nice -n 5 git s", 3),
    ("nice -n5 git s", 2),
    ("nice --adjustment=5 git s", 2),
    ("nice -- git s", 2),
    ("time git s", 1),
    ("time -p git s", 2),
    ("time -f %e git s", 3),
    ("time --format=%e git s", 2),
    ("stdbuf -o L git s", 3),
    ("stdbuf -oL git s", 2),
    ("stdbuf --output=L git s", 2),
    ("stdbuf -i0 -o0 -e0 git s", 4),
    ("nohup git s", 1),
    ("command git s", 1),
    ("command -p git s", 2),
    ("exec git s", 1),
    ("exec -a n git s", 3),
    ("exec -c -l git s", 3),
]


@pytest.mark.parametrize(
    ("shorthand", "start"),
    [pytest.param(shorthand, start, id=shorthand) for shorthand, start in _RESOLVED_COMMAND_START],
)
def test_wrapper_command_start_locates_the_command_word(shorthand: str, start: int) -> None:
    assert _wrapper_command_start(shorthand.split(), 0) == (start, False)


_UNRESOLVED_COMMAND_START = [
    "sudo -nu root git s",
    "sudo -h git s",
    "sudo --bogus git s",
    "timeout --bogus 10 git s",
    "timeout -x 10 git s",
    "nice -5 git s",
    "time --portability git s",
    "stdbuf -x L git s",
    "command -x git s",
    "exec -z git s",
    "nohup --bogus git s",
]


@pytest.mark.parametrize("shorthand", _UNRESOLVED_COMMAND_START)
def test_wrapper_command_start_stops_at_an_unmodelled_option(shorthand: str) -> None:
    assert _wrapper_command_start(shorthand.split(), 0) == (0, True)


def test_wrapper_command_start_unresolved_stops_at_the_wrapper_word_not_the_segment_start() -> (
    None
):
    argv = "FOO=1 sudo -h git s".split()
    assert _wrapper_command_start(argv, 1) == (1, True)


def test_wrapper_command_start_resolved_index_is_in_the_segment_domain() -> None:
    argv = "FOO=1 sudo -n git s".split()
    assert _wrapper_command_start(argv, 1) == (3, False)


@pytest.mark.parametrize(
    "shorthand",
    [
        "timeout",
        "timeout 10",
        "timeout -k 5 10",
        "sudo",
        "sudo -n",
        "nice -n 5",
        "nohup",
        "command",
        "command -v git",
        "command -V git",
        "command -p -v git",
    ],
)
def test_wrapper_without_a_command_runs_past_the_segment(shorthand: str) -> None:
    argv = shorthand.split()
    start, unresolved = _wrapper_command_start(argv, 0)
    assert start >= len(argv)
    assert not unresolved


@pytest.mark.parametrize(
    ("shorthand", "expected"),
    [
        pytest.param("sudo -D /x git s", ("/x", 2, 0), id="detached"),
        pytest.param("sudo --chdir=/x git s", ("/x", 1, len("--chdir=")), id="long-attached"),
        pytest.param("sudo -D/x git s", ("/x", 1, 2), id="short-attached"),
        pytest.param("sudo -n -D /x -u root git s", ("/x", 3, 0), id="among-other-options"),
        pytest.param("sudo -D /a -D /b git s", ("/b", 4, 0), id="last-detached-wins"),
        pytest.param(
            "sudo -D /a --chdir=/b git s", ("/b", 3, len("--chdir=")), id="last-spelling-wins"
        ),
        pytest.param("sudo -n git s", None, id="no-chdir"),
        pytest.param("sudo git s -D /x", None, id="after-the-command-short"),
        pytest.param("sudo git s --chdir=/x", None, id="after-the-command-long"),
    ],
)
def test_wrapper_chdir_value(shorthand: str, expected: tuple[str, int, int] | None) -> None:
    assert _wrapper_chdir_value(shorthand.split(), 0) == expected


def test_wrapper_chdir_value_for_a_wrapper_without_a_chdir_option_is_none() -> None:
    assert _wrapper_chdir_value("timeout 10 git s".split(), 0) is None
    assert _wrapper_chdir_value("nice -n 5 git s".split(), 0) is None


def test_wrapper_chdir_value_indexes_the_segment_not_the_wrapper() -> None:
    argv = "FOO=1 sudo -D /x git s".split()
    assert _wrapper_chdir_value(argv, 1) == ("/x", 3, 0)


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        pytest.param(tokens, expected, id=" ".join(tokens) or "empty")
        for tokens, expected in [
            (["-S", "git s"], True),
            (["-Sgit s"], True),
            (["--split-string", "git s"], True),
            (["--split-string=git s"], True),
            (["-i", "-S", "x"], True),
            (["-i", "A=1"], False),
            (["-u", "FOO"], False),
            (["--unset=FOO"], False),
            (["-C", "/x"], False),
            (["--ignore-signal=INT"], False),
            ([], False),
        ]
    ],
)
def test_env_splits_string(tokens: list[str], expected: bool) -> None:
    assert _env_splits_string(tokens) is expected


_OPAQUE_LAUNCHERS = [
    "xargs git push",
    "xargs -n1 gh pr view",
    "/usr/bin/xargs git push",
    "watch git s",
    "parallel git ::: s",
    "uvx t",
    "uv run git s",
    "uv run pytest",
    "pipx run t",
    "setsid git s",
    "ionice -c3 git s",
    "taskset 1 git s",
    "chrt -o 0 git s",
    "flock /tmp/l git s",
    "find . -exec git s {} ;",
    "find . -execdir git s {} ;",
    "find . -ok git s {} ;",
    "find . -okdir git s {} ;",
    "find . -name x -exec git s {} +",
    "node -e {P}",
    "node --eval {P}",
    "perl -e {P}",
    "perl -E {P}",
    "ruby -e {P}",
    "bash --bogus -c {P}",
    "sh -c",
    "zsh -lc",
    "python3 --bogus -c {P}",
    "python3 -c",
    "/usr/bin/python3.12 -Bc",
]
_RESOLVED_LAUNCHERS = [
    "git push",
    "pip install x",
    "find . -name x",
    "find . -type f",
    "uv",
    "uv pip install x",
    "uv sync",
    "pipx install x",
    "pipx",
    "node script.js",
    "perl script.pl",
    "bash script.sh",
    "bash -lc {P}",
    "bash -c {P}",
    "sh -ec {P}",
    "zsh -c {P}",
    "python3 -c {P}",
    "python3 -Bc {P}",
    "python3 -m pip",
    "python3 script.py -c x",
    "python3 script.py",
    "python script.py",
]


@pytest.mark.parametrize("shorthand", _OPAQUE_LAUNCHERS)
def test_launcher_position_unresolved_for_opaque_launchers(shorthand: str) -> None:
    assert _launcher_position_unresolved(_argv(shorthand)) is True


@pytest.mark.parametrize("shorthand", _RESOLVED_LAUNCHERS)
def test_launcher_position_resolved_for_direct_commands(shorthand: str) -> None:
    assert _launcher_position_unresolved(_argv(shorthand)) is False
