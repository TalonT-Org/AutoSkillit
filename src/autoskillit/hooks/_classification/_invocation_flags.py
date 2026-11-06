"""Invocation grammar for the commands that run other commands.

Members: _SHELL_INVOCATION_FLAG_SPEC, _PYTHON_INVOCATION_FLAG_SPEC,
_SHELL_INVOCATION_BOOLEAN_FLAGS, _PYTHON_INVOCATION_BOOLEAN_FLAGS,
shell_c_payload, python_c_program, _WrapperSpec, _WRAPPER_SPECS,
_wrapper_command_start, _wrapper_chdir_value, _env_splits_string,
_launcher_position_unresolved.

A shell or Python ``-c`` program is found through its bundled, stuck and
value-taking options (``bash -lc``, ``python3 -Bc``, ``-W error -c``), and a
wrapper's command word through its own option table. Anything the tables do not
model is *unresolved*: the position of the command it runs cannot be proven,
which is reported out of band so shared consumers keep their verb.

Leaf module: imports only ``_flag_arity_classification``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._flag_arity_classification import _FlagArity
else:
    if __package__:
        from . import _flag_arity_classification
    else:
        import _flag_arity_classification

    _FlagArity = _flag_arity_classification._FlagArity

# Flags that consume a following value when the shell/Python interpreter itself
# (not a heredoc/herestring body) reads a script from a positional operand. Only
# VALUE-arity flags are listed: any other `-`/`+`-prefixed token is skipped as
# boolean by the default bucket in `_interpreters._walk_invocation_flags`.
_SHELL_INVOCATION_FLAG_SPEC: dict[str, _FlagArity] = {
    "-o": _FlagArity.VALUE,
    "+o": _FlagArity.VALUE,
    "-O": _FlagArity.VALUE,
    "+O": _FlagArity.VALUE,
    "--rcfile": _FlagArity.VALUE,
    "--init-file": _FlagArity.VALUE,
    # Bash 5.2 help does not print --rcfile/--init-file's operand arity;
    # covered by the generative flag-spec test instead of the
    # live-`--help` contract test (see
    # tests/hooks/test_gh_api_flag_spec_contract.py).
    "-c": _FlagArity.VALUE,
}
_PYTHON_INVOCATION_FLAG_SPEC: dict[str, _FlagArity] = {
    "-c": _FlagArity.VALUE,
    "-m": _FlagArity.VALUE,
    "-W": _FlagArity.VALUE,
    "-X": _FlagArity.VALUE,
    "--check-hash-based-pycs": _FlagArity.VALUE,
}
# Long boolean options. Short boolean letters need no entry: an unknown short
# letter is skipped as a boolean.
_SHELL_INVOCATION_BOOLEAN_FLAGS: frozenset[str] = frozenset(
    {
        "--debug",
        "--norc",
        "--noprofile",
        "--login",
        "--posix",
        "--restricted",
        "--verbose",
        "--noediting",
        "--dump-strings",
        "--dump-po-strings",
        "--version",
        "--help",
    }
)
_PYTHON_INVOCATION_BOOLEAN_FLAGS: frozenset[str] = frozenset({"--help", "--version"})
_SHELL_VALUE_LETTERS = "oO"
_PYTHON_VALUE_LETTERS = "WX"


def shell_c_payload(args: Sequence[str]) -> tuple[str | None, bool]:
    """The ``-c`` program of a shell's argv as ``(program, unresolved)``.

    A bundle containing ``c`` selects payload mode and the program is the first
    operand. ``o``/``O`` (and ``+o``/``+O``) take a value. An unknown long
    option, or ``-c`` with no program, is unresolved.
    """
    payload_mode = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token.startswith("--"):
            if token in _SHELL_INVOCATION_BOOLEAN_FLAGS:
                index += 1
            elif token in _SHELL_INVOCATION_FLAG_SPEC:
                index += 2
            else:
                return None, True
            continue
        if token[:1] not in ("-", "+") or len(token) == 1:
            break
        letters = token[1:]
        payload_mode = payload_mode or "c" in letters
        index += 2 if letters[-1] in _SHELL_VALUE_LETTERS else 1
    if not payload_mode:
        return None, False
    return (args[index], False) if index < len(args) else (None, True)


def _python_short_cluster(args: Sequence[str], index: int) -> tuple[str, str | None, int]:
    """Scan the short options of ``args[index]`` as ``(status, program, next_index)``.

    *status* is ``program`` (``-c`` found), ``stop`` (``-m`` ends the scan),
    ``unresolved`` (``-c`` has no program) or ``continue``.
    """
    letters = args[index][1:]
    for position, letter in enumerate(letters):
        rest = letters[position + 1 :]
        if letter == "c":
            if rest:
                return "program", rest, index + 1
            if index + 1 < len(args):
                return "program", args[index + 1], index + 2
            return "unresolved", None, index + 1
        if letter == "m":
            return "stop", None, index + 1
        if letter in _PYTHON_VALUE_LETTERS:
            return "continue", None, index + (1 if rest else 2)
    return "continue", None, index + 1


def python_c_program(args: Sequence[str]) -> tuple[str | None, bool]:
    """The ``-c`` program of a Python argv as ``(program, unresolved)``.

    Handles bundles (``-Bc``), a stuck program (``-cPROGRAM``) and the
    value-taking ``-W``/``-X``. ``-m`` or a script operand ends the scan with no
    program. An unknown long option, or ``-c`` with no program, is unresolved.
    """
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--" or token[:1] != "-" or len(token) == 1:
            return None, False
        if token.startswith("--"):
            if token in _PYTHON_INVOCATION_BOOLEAN_FLAGS:
                index += 1
            elif token in _PYTHON_INVOCATION_FLAG_SPEC:
                index += 2
            else:
                return None, True
            continue
        status, program, index = _python_short_cluster(args, index)
        if status == "program":
            return program, False
        if status in ("stop", "unresolved"):
            return None, status == "unresolved"
    return None, False


@dataclass(frozen=True, slots=True)
class _WrapperSpec:
    booleans: frozenset[str]
    values: frozenset[str]
    leading_operands: int
    chdir: frozenset[str]


_NO_OPTIONS: frozenset[str] = frozenset()

# Each row was checked against the binary's own `--help` (coreutils 9.4, sudo
# 1.9). An option whose short form carries two meanings (`sudo -h` is `--help`
# and `--host`) stays out of the table so it falls to the unmodelled rule: a
# modelled-but-wrong arity silently mislocates the command word, while an
# unmodelled option fails closed. The one exception is an `=`-joined token
# (`--host=h`): it is a single token whatever the option's arity.
_WRAPPER_SPECS: Mapping[str, _WrapperSpec] = {
    "timeout": _WrapperSpec(
        frozenset({"--foreground", "--preserve-status", "-v", "--verbose"}),
        frozenset({"-k", "--kill-after", "-s", "--signal"}),
        1,
        _NO_OPTIONS,
    ),
    "sudo": _WrapperSpec(
        frozenset(
            {
                "-n",
                "--non-interactive",
                "-E",
                "--preserve-env",
                "-H",
                "--set-home",
                "-S",
                "--stdin",
                "-b",
                "--background",
                "-i",
                "--login",
                "-s",
                "--shell",
            }
        ),
        frozenset(
            {
                "-u",
                "--user",
                "-g",
                "--group",
                "-C",
                "--close-from",
                "-p",
                "--prompt",
                "-r",
                "--role",
                "-t",
                "--type",
                "-T",
                "--command-timeout",
                "-U",
                "--other-user",
            }
        ),
        0,
        frozenset({"-D", "--chdir"}),
    ),
    "nice": _WrapperSpec(_NO_OPTIONS, frozenset({"-n", "--adjustment"}), 0, _NO_OPTIONS),
    "time": _WrapperSpec(
        frozenset({"-p", "-a", "-v", "-q"}),
        frozenset({"-f", "--format", "-o", "--output"}),
        0,
        _NO_OPTIONS,
    ),
    "stdbuf": _WrapperSpec(
        _NO_OPTIONS,
        frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
        0,
        _NO_OPTIONS,
    ),
    "command": _WrapperSpec(frozenset({"-p"}), _NO_OPTIONS, 0, _NO_OPTIONS),
    "exec": _WrapperSpec(frozenset({"-c", "-l"}), frozenset({"-a"}), 0, _NO_OPTIONS),
    "nohup": _WrapperSpec(_NO_OPTIONS, _NO_OPTIONS, 0, _NO_OPTIONS),
}


def _wrapper_option_width(spec: _WrapperSpec, token: str) -> int | None:
    """Tokens a wrapper option occupies (1 or 2), or None when its width is unknown.

    An ``=``-joined token is one token whether or not the table models its option.
    """
    values = spec.values | spec.chdir
    if "=" in token:
        return 1
    if token in spec.booleans:
        return 1
    if token in values:
        return 2
    if not token.startswith("--") and len(token) > 2 and token[:2] in values:
        return 1
    return None


def _wrapper_command_start(argv: Sequence[str], index: int) -> tuple[int, bool]:
    """Index of the command word a wrapper at *index* runs, and whether that is unresolved.

    ``--`` ends wrapper options. Any other option the wrapper's table does not
    model stops the walk at the wrapper word and reports the position unresolved.
    ``command -v`` describes a command rather than running one, so it has none.
    """
    name = argv[index]
    spec = _WRAPPER_SPECS[name]
    position = index + 1
    while position < len(argv):
        token = argv[position]
        if token == "--":
            position += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if name == "command" and token in ("-v", "-V"):
            return len(argv), False
        width = _wrapper_option_width(spec, token)
        if width is None:
            return index, True
        position += width
    return position + spec.leading_operands, False


def _wrapper_chdir_value(argv: Sequence[str], index: int) -> tuple[str, int, int] | None:
    """A wrapper's last chdir value (``sudo -D``) as ``(value, token_index, prefix_length)``."""
    chdir = _WRAPPER_SPECS[argv[index]].chdir
    end, _unresolved = _wrapper_command_start(argv, index)
    found: tuple[str, int, int] | None = None
    position = index + 1
    while position < end:
        head, has_value, value = argv[position].partition("=")
        if has_value and head in chdir:
            found = (value, position, len(head) + 1)
        elif argv[position] in chdir and position + 1 < end:
            found = (argv[position + 1], position + 1, 0)
            position += 1
        elif len(argv[position]) > 2 and argv[position][:2] in chdir:
            found = (argv[position][2:], position, 2)
        position += 1
    return found


def _env_splits_string(tokens: Sequence[str]) -> bool:
    """Whether the consumed ``env`` options include ``-S``/``--split-string``."""
    return any(token.startswith(("-S", "--split-string")) for token in tokens)


_OPAQUE_LAUNCHERS: frozenset[str] = frozenset(
    {
        "xargs",
        "watch",
        "parallel",
        "uvx",
        "setsid",
        "ionice",
        "taskset",
        "chrt",
        "flock",
    }
)
_FIND_EXEC_FLAGS: frozenset[str] = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_EVAL_INTERPRETERS: frozenset[str] = frozenset({"node", "perl", "ruby"})
_EVAL_FLAGS: frozenset[str] = frozenset({"-e", "-E", "--eval"})
_SHELL_NAME_RE = re.compile(r"(?:bash|sh|zsh|dash)[\d.]*", re.IGNORECASE)
_PYTHON_NAME_RE = re.compile(r"python(?:3(?:\.\d+)?)?", re.IGNORECASE)


def _launcher_position_unresolved(argv: Sequence[str]) -> bool:
    """Whether *argv* (starting at its command word) runs a command that cannot be located.

    Opaque launchers (``xargs``, ``find -exec``, ``uv run``, ``node -e``, ...) and a
    shell or Python ``-c`` whose program cannot be found hide the command they
    run. The launcher word stays the verb for every shared consumer.
    """
    name = os.path.basename(argv[0])
    args = argv[1:]
    if name in _OPAQUE_LAUNCHERS:
        return True
    if name in ("uv", "pipx"):
        return args[:1] == ["run"]
    if name == "find":
        return any(arg in _FIND_EXEC_FLAGS for arg in args)
    if name in _EVAL_INTERPRETERS:
        return any(arg in _EVAL_FLAGS for arg in args)
    if _SHELL_NAME_RE.fullmatch(name):
        return shell_c_payload(args)[1]
    return bool(_PYTHON_NAME_RE.fullmatch(name)) and python_c_program(args)[1]
