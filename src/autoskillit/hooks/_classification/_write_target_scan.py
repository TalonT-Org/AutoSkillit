"""Scope-aware write-target scan for evaluated shell and Python commands."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._tokenizer import ArgvToken


WRITE_VERBS: frozenset[str] = frozenset(
    {"sed", "tee", "mv", "cp", "patch", "install", "rm", "unlink"}
)
_PSEUDO_DEVICE_PATHS: frozenset[str] = frozenset(
    {"/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/stdin"}
)
UNRESOLVED_WRITE_TARGET_REMEDIATION = (
    "Write targets must be literal paths: substitute variable, command-substitution, "
    "backtick and tilde values into the command text (e.g. run `date +%Y-%m-%d_%H%M%S` "
    "once and paste the printed value) instead of writing through them."
)


@dataclass(frozen=True, slots=True)
class WriteTargetScan:
    targets: tuple[str, ...]
    unresolved: bool
    parseable: bool
    has_write: bool

    def __post_init__(self) -> None:
        if not self.parseable and self.targets:
            raise ValueError("WriteTargetScan.targets must be empty when parseable is False")
        if not self.has_write and self.targets:
            raise ValueError("WriteTargetScan.targets must be empty when has_write is False")


@dataclass(slots=True)
class _ShellDirectoryState:
    cwd: str
    cdpath_unknown: bool


def _scope_state(
    scopes: dict[tuple[int, ...], _ShellDirectoryState], path: tuple[int, ...]
) -> _ShellDirectoryState:
    for length in range(1, len(path) + 1):
        prefix = path[:length]
        if prefix not in scopes:
            inherited = scopes[prefix[:-1]]
            scopes[prefix] = _ShellDirectoryState(inherited.cwd, inherited.cdpath_unknown)
    return scopes[path]


def _shell_builtin_prefix(prefix: list[str]) -> bool:
    """True when *prefix* contains no wrapper command — i.e. is the unwrapped shell-builtin form.

    The polarity is ``unwrapped``: returns True only when none of the wrapper
    tokens (``env``, ``sudo``, ``nice``, ``nohup``, ``timeout``, ``stdbuf``)
    appears in the prefix.
    """
    return not any(
        token in {"env", "sudo", "nice", "nohup", "timeout", "stdbuf"} for token in prefix
    )


def _cdpath_mutated(segment: list[str], start: int | None) -> bool:
    prefix = segment if start is None else segment[:start]
    if not _shell_builtin_prefix(prefix):
        return False
    verb = segment[start] if start is not None else ""
    shell_builtin = verb in {
        "cd",
        "pushd",
        "popd",
        "eval",
        ".",
        "source",
        "export",
        "unset",
        "readonly",
        "declare",
        "typeset",
        "local",
        "read",
    }
    if any(
        _classification._is_posix_assignment(token) and token.partition("=")[0] == "CDPATH"
        for token in prefix
    ) and (start is None or shell_builtin):
        return True
    if start is None or verb not in {
        "export",
        "unset",
        "readonly",
        "declare",
        "typeset",
        "local",
        "read",
    }:
        return False
    return any(token == "CDPATH" or token.startswith("CDPATH=") for token in segment[start + 1 :])


def _apply_directory_command(
    verb: str,
    argv: list[str],
    argv_tokens: Sequence[ArgvToken] | None,
    state: _ShellDirectoryState,
) -> None:
    if verb == "popd":
        state.cwd = ""
        return

    if verb == "pushd":
        if len(argv) != 2 or argv[1].startswith(("+", "-")):
            state.cwd = ""
            return
        path_index = 1
    else:
        path_index = 1
        while path_index < len(argv) and argv[path_index] in {"-L", "-P", "-e", "-@"}:
            path_index += 1
        after_double_dash = False
        if path_index < len(argv) and argv[path_index] == "--":
            path_index += 1
            after_double_dash = True
        if path_index == len(argv):
            home = os.path.expanduser("~")
            state.cwd = home if os.path.isabs(home) else ""
            return
        if path_index != len(argv) - 1 or (
            argv[path_index] == "-" or (argv[path_index].startswith("-") and not after_double_dash)
        ):
            state.cwd = ""
            return

    path = argv[path_index]
    if state.cdpath_unknown and not os.path.isabs(path):
        state.cwd = ""
        return
    state.cwd = (
        _classification.resolve_write_target(
            path, state.cwd, shell_source=_classification._shell_source(argv_tokens, path_index)
        )
        or ""
    )


def _seed_child_scope(
    path: tuple[int, ...] | None,
    scopes: dict[tuple[int, ...], _ShellDirectoryState],
    pending_children: dict[
        tuple[int, ...], tuple[_ShellDirectoryState, _ShellDirectoryState | None]
    ],
) -> None:
    if path is None:
        return
    for length in range(len(path) - 1, -1, -1):
        if path[length] >= 0:
            continue
        owner = path[:length]
        pending = pending_children.get(owner)
        if pending is None:
            continue
        child_scope = path[: length + 1]
        snapshot, invoked = pending
        if child_scope not in scopes:
            seed = invoked or snapshot
            scopes[child_scope] = _ShellDirectoryState(seed.cwd, seed.cdpath_unknown)
        pending_children[owner] = (snapshot, None)
        return


def _record_state(
    record: _classification.EvaluatedSegment,
    scopes: dict[tuple[int, ...], _ShellDirectoryState],
    overridden_scopes: set[tuple[int, ...]],
    pending_children: dict[
        tuple[int, ...], tuple[_ShellDirectoryState, _ShellDirectoryState | None]
    ],
) -> _ShellDirectoryState:
    path = record.subshell_path
    _seed_child_scope(path, scopes, pending_children)
    if path is None:
        state = _ShellDirectoryState("", True)
        if record.cwd_override is not None:
            state.cwd = _classification.resolve_write_target(record.cwd_override, "") or ""
        return state
    if record.cwd_override is not None:
        negative_components = [index for index, component in enumerate(path) if component < 0]
        override_scope = path[: negative_components[-1] + 1] if negative_components else path
        if override_scope not in overridden_scopes:
            override_state = _scope_state(scopes, override_scope)
            override_state.cwd = (
                _classification.resolve_write_target(record.cwd_override, override_state.cwd) or ""
            )
            overridden_scopes.add(override_scope)
    return _scope_state(scopes, path)


def _apply_shell_builtin(
    executable: list[str],
    start: int,
    argv_tokens: Sequence[ArgvToken] | None,
    state: _ShellDirectoryState,
) -> bool:
    if argv_tokens is None or not _shell_builtin_prefix(executable[:start]):
        return False
    if start >= len(executable):
        state.cwd = ""
        state.cdpath_unknown = True
        return True
    argv = executable[start:]
    verb = argv[0]
    if verb in {"cd", "pushd", "popd"}:
        _apply_directory_command(verb, argv, argv_tokens[start:], state)
        return True
    if verb in {".", "source"}:
        state.cwd = ""
        state.cdpath_unknown = True
        return True
    if verb == "eval":
        if any(
            "$" in token.raw_span or "`" in token.raw_span for token in argv_tokens[start + 1 :]
        ):
            state.cwd = ""
            state.cdpath_unknown = True
        return True
    return False


def _invoked_child_state(
    verb: str,
    verb_cwd: str,
    state: _ShellDirectoryState,
    path: tuple[int, ...] | None,
) -> _ShellDirectoryState | None:
    if path is None or verb_cwd == state.cwd:
        return None
    if _classification._is_shell_interpreter(verb) or re.fullmatch(
        r"python(?:3(?:\.\d+)?)?", _classification._normalize_executable(verb)
    ):
        return _ShellDirectoryState(verb_cwd, state.cdpath_unknown)
    return None


def _scan_executable(
    executable: list[str],
    argv_tokens: Sequence[ArgvToken] | None,
    state: _ShellDirectoryState,
    path: tuple[int, ...] | None,
) -> tuple[list[str], bool, bool, _ShellDirectoryState | None]:
    start = _classification._verb_start_index(executable)
    if argv_tokens is not None and _cdpath_mutated(executable, start):
        state.cdpath_unknown = True
    if start is None or _apply_shell_builtin(executable, start, argv_tokens, state):
        return [], False, False, None
    if _classification.is_gh_command(executable):
        return [], False, False, None
    argv = executable[start:]
    verb = argv[0]
    verb_cwd = _classification._wrapped_verb_cwd(executable, start, state.cwd, argv_tokens)
    invoked = _invoked_child_state(verb, verb_cwd, state, path)
    verb_tokens = None if argv_tokens is None else argv_tokens[start:]
    if verb in WRITE_VERBS:
        targets, unresolved = _classification.extract_write_verb_targets(
            verb, argv, verb_cwd, argv_tokens=verb_tokens
        )
        return targets, unresolved, True, invoked
    if verb == "git" or verb.endswith("/git"):
        targets, unresolved, has_write = _classification._git_write_targets(
            argv, verb_cwd, executable[:start], verb_tokens
        )
        return targets, unresolved, has_write, invoked
    return [], False, False, invoked


def scan_write_targets(command: str, cwd: str) -> WriteTargetScan:
    """Classify literal write targets and unresolved writes in evaluated shell commands."""
    records = _classification._all_evaluated_segments_with_provenance_impl(
        command, include_process_substitutions=True
    )
    if records is None:
        return WriteTargetScan((), False, False, False)

    targets: list[str] = []
    unresolved = False
    has_write = False
    scopes: dict[tuple[int, ...], _ShellDirectoryState] = {
        (): _ShellDirectoryState(cwd, bool(os.environ.get("CDPATH")))
    }
    overridden_scopes: set[tuple[int, ...]] = set()
    pending_children: dict[
        tuple[int, ...], tuple[_ShellDirectoryState, _ShellDirectoryState | None]
    ] = {}
    for record in records:
        state = _record_state(record, scopes, overridden_scopes, pending_children)
        pre_command_state = _ShellDirectoryState(state.cwd, state.cdpath_unknown)
        partition = _classification._partition_output_redirect_indices(
            record.tokens,
            cwd=state.cwd,
            redirect_syntax=record.redirect_syntax,
            argv_tokens=record.argv_tokens,
        )
        unresolved |= partition.unresolved
        has_write |= bool(partition.file_redirect_count)

        executable = [record.tokens[index] for index in partition.segments]
        executable_tokens = (
            None
            if record.argv_tokens is None
            else [record.argv_tokens[index] for index in partition.segments]
        )
        verb_targets, verb_unresolved, verb_has_write, invoked = _scan_executable(
            executable, executable_tokens, state, record.subshell_path
        )
        targets.extend(verb_targets)
        targets.extend(partition.targets)
        unresolved |= verb_unresolved
        has_write |= verb_has_write
        if record.subshell_path is not None:
            pending_children[record.subshell_path] = (pre_command_state, invoked)
    return WriteTargetScan(
        tuple(dict.fromkeys(path for path in targets if path not in _PSEUDO_DEVICE_PATHS)),
        unresolved,
        True,
        has_write,
    )


if TYPE_CHECKING:
    from autoskillit.hooks._runtime import _command_classification as _classification
elif __package__ == "autoskillit.hooks._classification":
    from .._runtime import _command_classification as _classification
else:
    import _command_classification as _classification
