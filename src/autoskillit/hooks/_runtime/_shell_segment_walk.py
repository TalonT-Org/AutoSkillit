"""Scope-aware walk over evaluated shell segments: the single cwd authority.

Members: ShellDirectoryState, WalkedSegment, walk_evaluated_segments.

Every segment that will actually execute is visited once, in order, with the
working directory in effect for it. ``verb_cwd`` is the shell's logical
directory (what the write scan consumes); ``cwd_certain`` additionally says
whether that directory is *proven*: a ``cd`` counts only when it certainly ran
and succeeded before the segment. A failed, dynamic, conditional, piped,
backgrounded, function-body or compound-body ``cd`` leaves the cwd uncertain,
and an uncertain cwd must never be used to attribute a command to a
repository.

The facade and ``_interpreters`` are reached only through
``_classification_module()``: a top-level import would cycle, because the
facade imports ``_write_target_scan`` (which imports this module) at its bottom.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._invocation_flags import (
        _PYTHON_NAME_RE,
        _WRAPPER_SPECS,
    )
    from autoskillit.hooks._classification._output_redirect import OutputRedirectPartition
    from autoskillit.hooks._classification._tokenizer import (
        _LOGICAL_CONNECTORS,
        ArgvToken,
        EvaluatedSegment,
    )
else:
    if __package__:
        from .._classification import _invocation_flags, _tokenizer
    else:
        from _classification import _invocation_flags, _tokenizer

    _PYTHON_NAME_RE = _invocation_flags._PYTHON_NAME_RE
    _WRAPPER_SPECS = _invocation_flags._WRAPPER_SPECS
    _LOGICAL_CONNECTORS = _tokenizer._LOGICAL_CONNECTORS

_DIRECTORY_COMMANDS: frozenset[str] = frozenset({"cd", "pushd", "popd"})
_TAINTING_FIRST_TOKENS: frozenset[str] = frozenset(
    {"if", "while", "until", "for", "select", "case", "then", "do", "else", "elif", "{"}
)
# ``time`` and ``command`` run a shell builtin in the current shell, so ``time cd d``
# still changes the directory; every other wrapper starts a process, where ``cd`` is
# not a command.
_BUILTIN_TRANSPARENT_WRAPPERS: frozenset[str] = frozenset({"time", "command"})
_UNWRAPPED_BLOCKERS: frozenset[str] = (
    frozenset({"env", *_WRAPPER_SPECS}) - _BUILTIN_TRANSPARENT_WRAPPERS
)


@dataclass(slots=True)
class ShellDirectoryState:
    cwd: str
    cdpath_unknown: bool
    cwd_certain: bool = False
    # True while the cwd was reached through an unbroken ``&&`` chain from a cd.
    chain_pending: bool = False
    # True once the scope has run a conditionally or asynchronously joined command.
    scope_tainted: bool = False
    # True when the last directory change did not depend on the previous cwd.
    cwd_independent: bool = False

    def fork(self) -> ShellDirectoryState:
        """State a child scope starts from: the cwd, not the parent's control flow."""
        return ShellDirectoryState(self.cwd, self.cdpath_unknown, self.cwd_certain)


@dataclass(frozen=True, slots=True)
class WalkedSegment:
    record: EvaluatedSegment
    partition: OutputRedirectPartition
    executable: list[str]
    executable_tokens: list[ArgvToken] | None
    start: int | None
    verb_cwd: str
    cwd_certain: bool
    handled_builtin: bool
    position_unresolved: bool


def _classification_module() -> ModuleType:
    if __package__:
        from . import _command_classification as packaged_classification

        return packaged_classification
    import _command_classification as standalone_classification

    return standalone_classification


def _ensure_scope_state(
    scopes: dict[tuple[int, ...], ShellDirectoryState], path: tuple[int, ...]
) -> ShellDirectoryState:
    for length in range(1, len(path) + 1):
        prefix = path[:length]
        if prefix not in scopes:
            scopes[prefix] = scopes[prefix[:-1]].fork()
    return scopes[path]


def _unwrapped_prefix(prefix: list[str]) -> bool:
    """True when *prefix* contains no wrapper command — i.e. is the unwrapped shell-builtin form.

    The polarity is ``unwrapped``: returns True only when no process-starting
    wrapper (``env`` and the ``_WRAPPER_SPECS`` commands, bar the builtin-transparent
    ``time`` and ``command``) appears in the prefix.
    """
    return not any(token in _UNWRAPPED_BLOCKERS for token in prefix)


def _cdpath_setting(segment: list[str], start: int | None) -> tuple[bool, bool] | None:
    _classification = _classification_module()

    prefix = segment if start is None else segment[:start]
    if not _unwrapped_prefix(prefix):
        return None
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
    assignments = [
        token.partition("=")[2]
        for token in prefix
        if _classification._is_posix_assignment(token) and token.partition("=")[0] == "CDPATH"
    ]
    if assignments and (start is None or shell_builtin):
        return bool(assignments[-1]), verb in {"cd", "pushd", "popd"}
    if start is None or verb not in {
        "export",
        "unset",
        "readonly",
        "declare",
        "typeset",
        "local",
        "read",
    }:
        return None
    for token in segment[start + 1 :]:
        if token.startswith("CDPATH="):
            return bool(token.partition("=")[2]), False
        if token == "CDPATH":
            if verb == "unset":
                return False, False
            if verb != "export":
                return True, False
    return None


def _directory_command_path_index(argv: list[str]) -> int | None:
    """Index of the ``cd`` operand in *argv*; ``len(argv)`` when absent, None when unknowable."""
    if argv[0] == "pushd":
        return None if len(argv) != 2 or argv[1].startswith(("+", "-")) else 1
    path_index = 1
    while path_index < len(argv) and argv[path_index] in {"-L", "-P", "-e", "-@"}:
        path_index += 1
    after_double_dash = False
    if path_index < len(argv) and argv[path_index] == "--":
        path_index += 1
        after_double_dash = True
    if path_index == len(argv):
        return path_index
    if path_index != len(argv) - 1 or (
        argv[path_index] == "-" or (argv[path_index].startswith("-") and not after_double_dash)
    ):
        return None
    return path_index


def _apply_directory_command(
    verb: str,
    argv: list[str],
    argv_tokens: Sequence[ArgvToken] | None,
    state: ShellDirectoryState,
) -> None:
    _classification = _classification_module()

    state.cwd_independent = False
    if verb == "popd":
        state.cwd = ""
        return
    path_index = _directory_command_path_index(argv)
    if path_index is None:
        state.cwd = ""
        return
    if path_index == len(argv):
        home = os.path.expanduser("~")
        state.cwd = home if os.path.isabs(home) else ""
        state.cwd_independent = bool(state.cwd)
        return
    path = argv[path_index]
    if state.cdpath_unknown and not os.path.isabs(path):
        state.cwd = ""
        return
    source = _classification._shell_source(argv_tokens, path_index)
    state.cwd = _classification.resolve_write_target(path, state.cwd, shell_source=source) or ""
    state.cwd_independent = (
        _classification.resolve_write_target(path, "", shell_source=source) is not None
    )


def _seed_child_scope(
    path: tuple[int, ...] | None,
    scopes: dict[tuple[int, ...], ShellDirectoryState],
    pending_children: dict[
        tuple[int, ...], tuple[ShellDirectoryState, ShellDirectoryState | None]
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
            scopes[child_scope] = (invoked or snapshot).fork()
        pending_children[owner] = (snapshot, None)
        return


def _override_state(state: ShellDirectoryState, override: str) -> None:
    """Apply a Python ``cwd=`` override: certain when absolute, or relative to a certain cwd."""
    _classification = _classification_module()

    independent = _classification.resolve_write_target(override, "") is not None
    state.cwd = _classification.resolve_write_target(override, state.cwd) or ""
    state.cwd_certain = bool(state.cwd) and (independent or state.cwd_certain)


def _record_state(
    record: EvaluatedSegment,
    scopes: dict[tuple[int, ...], ShellDirectoryState],
    overridden_scopes: set[tuple[int, ...]],
    pending_children: dict[
        tuple[int, ...], tuple[ShellDirectoryState, ShellDirectoryState | None]
    ],
) -> ShellDirectoryState:
    path = record.subshell_path
    _seed_child_scope(path, scopes, pending_children)
    if path is None:
        state = ShellDirectoryState("", True)
        if record.cwd_override is not None:
            _override_state(state, record.cwd_override)
        return state
    if record.cwd_override is not None:
        negative_components = [index for index, component in enumerate(path) if component < 0]
        override_scope = path[: negative_components[-1] + 1] if negative_components else path
        if override_scope not in overridden_scopes:
            _override_state(_ensure_scope_state(scopes, override_scope), record.cwd_override)
            overridden_scopes.add(override_scope)
    return _ensure_scope_state(scopes, path)


def _apply_shell_builtin(
    executable: list[str],
    start: int,
    argv_tokens: Sequence[ArgvToken] | None,
    state: ShellDirectoryState,
) -> bool:
    if argv_tokens is None or not _unwrapped_prefix(executable[:start]):
        return False
    if start >= len(executable):
        state.cwd = ""
        state.cdpath_unknown = True
        return True
    argv = executable[start:]
    verb = argv[0]
    if verb in _DIRECTORY_COMMANDS:
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
    verb_certain: bool,
    state: ShellDirectoryState,
    path: tuple[int, ...] | None,
) -> ShellDirectoryState | None:
    _classification = _classification_module()

    if path is None or verb_cwd == state.cwd:
        return None
    if _classification._is_shell_interpreter(verb) or _PYTHON_NAME_RE.fullmatch(
        _classification._normalize_executable(verb)
    ):
        return ShellDirectoryState(verb_cwd, state.cdpath_unknown, verb_certain)
    return None


def _connector(record: EvaluatedSegment) -> str:
    return record.syntax.connector if record.syntax is not None else ""


def _next_scope_connector(records: Sequence[EvaluatedSegment], position: int) -> str:
    """Connector of the next record in the same scope; child-scope records are skipped."""
    scope = records[position].subshell_path
    for later in records[position + 1 :]:
        if later.subshell_path == scope:
            return _connector(later)
    return ""


def _apply_connector_rules(state: ShellDirectoryState, record: EvaluatedSegment) -> None:
    syntax = record.syntax
    if (
        _connector(record) in _LOGICAL_CONNECTORS
        or (syntax is not None and (syntax.function_body or syntax.after_keyword))
        or record.tokens[0] in _TAINTING_FIRST_TOKENS
    ):
        state.scope_tainted = True
    if state.chain_pending and _connector(record) != "&&":
        state.cwd_certain = False
        state.chain_pending = False


def _settle_directory_command(
    state: ShellDirectoryState,
    record: EvaluatedSegment,
    next_connector: str,
    parent_certain: bool,
) -> None:
    """Decide whether a ``cd``/``pushd``/``popd`` certainly ran before what follows."""
    connector = _connector(record)
    piped = record.syntax is not None and record.syntax.piped_from_previous
    if not state.cwd or not (state.cwd_independent or parent_certain):
        state.cwd_certain = False
        state.chain_pending = False
        return
    # An unbroken ``&&`` chain runs a segment only if the cd ran and succeeded.
    chained = connector != "||" and not piped and next_connector == "&&"
    certain = (
        connector in {"", ";"}
        and not state.scope_tainted
        and not piped
        and next_connector not in {"|", "&"}
        and os.path.isdir(state.cwd)
    )
    state.cwd_certain = chained or certain
    state.chain_pending = chained


def _unmodelled_directory_command(executable: list[str], start: int) -> bool:
    verb = executable[start]
    if verb == "builtin":
        return len(executable) > start + 1 and executable[start + 1] in _DIRECTORY_COMMANDS
    return verb in _DIRECTORY_COMMANDS and "command" in executable[:start]


def _walk_executable(
    record: EvaluatedSegment,
    partition: OutputRedirectPartition,
    executable: list[str],
    executable_tokens: list[ArgvToken] | None,
    state: ShellDirectoryState,
    next_connector: str,
) -> tuple[WalkedSegment, ShellDirectoryState | None]:
    _classification = _classification_module()

    def walked(
        start: int | None,
        verb_cwd: str,
        certain: bool,
        handled: bool,
        position_unresolved: bool = False,
    ) -> WalkedSegment:
        return WalkedSegment(
            record,
            partition,
            executable,
            executable_tokens,
            start,
            verb_cwd,
            certain and bool(verb_cwd),
            handled,
            position_unresolved,
        )

    start, unresolved = _classification._command_position(executable)
    setting = _cdpath_setting(executable, start) if executable_tokens is not None else None
    previous_cdpath = state.cdpath_unknown
    if setting is not None:
        state.cdpath_unknown = setting[0]
    if start is None:
        return walked(None, state.cwd, state.cwd_certain, False, unresolved), None
    parent_certain = state.cwd_certain
    handled = _apply_shell_builtin(executable, start, executable_tokens, state)
    if setting is not None and setting[1]:
        state.cdpath_unknown = previous_cdpath
    if handled and executable[start] in _DIRECTORY_COMMANDS:
        _settle_directory_command(state, record, next_connector, parent_certain)
    if _unmodelled_directory_command(executable, start):
        state.cwd_certain = False
        state.chain_pending = False
    if handled or _classification.is_gh_command(executable):
        return walked(start, state.cwd, state.cwd_certain, handled, unresolved), None
    verb_cwd = _classification._wrapped_verb_cwd(executable, start, state.cwd, executable_tokens)
    absolute_override = _classification._wrapped_verb_cwd(executable, start, "", executable_tokens)
    verb_certain = bool(verb_cwd) and (bool(absolute_override) or state.cwd_certain)
    invoked = _invoked_child_state(
        executable[start], verb_cwd, verb_certain, state, record.subshell_path
    )
    return walked(start, verb_cwd, verb_certain, False, unresolved), invoked


def walk_evaluated_segments(command: str, cwd: str) -> list[WalkedSegment] | None:
    """Walk every evaluated segment of *command*, or None when it cannot be tokenized."""
    _classification = _classification_module()

    records = _classification._all_evaluated_segments_with_provenance_impl(
        command, include_process_substitutions=True
    )
    if records is None:
        return None
    scopes: dict[tuple[int, ...], ShellDirectoryState] = {
        (): ShellDirectoryState(cwd, bool(os.environ.get("CDPATH")), bool(cwd))
    }
    overridden_scopes: set[tuple[int, ...]] = set()
    pending_children: dict[
        tuple[int, ...], tuple[ShellDirectoryState, ShellDirectoryState | None]
    ] = {}
    segments: list[WalkedSegment] = []
    for position, record in enumerate(records):
        state = _record_state(record, scopes, overridden_scopes, pending_children)
        _apply_connector_rules(state, record)
        pre_command_state = state.fork()
        partition = _classification._partition_output_redirect_indices(
            record.tokens,
            cwd=state.cwd,
            redirect_syntax=record.redirect_syntax,
            argv_tokens=record.argv_tokens,
        )
        executable = [record.tokens[index] for index in partition.segments]
        executable_tokens = (
            None
            if record.argv_tokens is None
            else [record.argv_tokens[index] for index in partition.segments]
        )
        segment, invoked = _walk_executable(
            record,
            partition,
            executable,
            executable_tokens,
            state,
            _next_scope_connector(records, position),
        )
        segments.append(segment)
        if record.subshell_path is not None:
            pending_children[record.subshell_path] = (pre_command_state, invoked)
    return segments
