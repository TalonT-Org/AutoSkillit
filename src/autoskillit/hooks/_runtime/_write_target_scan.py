"""Scope-aware write-target scan for evaluated shell and Python commands."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._tokenizer import ArgvToken


WRITE_VERBS: frozenset[str] = frozenset({"sed", "tee", "mv", "cp", "patch", "rm", "unlink"})
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
    entry_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.parseable and (self.targets or self.entry_targets):
            raise ValueError(
                "WriteTargetScan targets and entry_targets must be empty when parseable is False"
            )
        if not self.has_write and (self.targets or self.entry_targets):
            raise ValueError(
                "WriteTargetScan targets and entry_targets must be empty when has_write is False"
            )


@dataclass(slots=True)
class _ShellDirectoryState:
    cwd: str
    cdpath_unknown: bool


def _classification_module() -> ModuleType:
    if __package__:
        from . import _command_classification as packaged_classification

        return packaged_classification
    import _command_classification as standalone_classification

    return standalone_classification


@dataclass(frozen=True, slots=True)
class _DestinationOperands:
    operands: tuple[int, ...]
    target_directory: tuple[str, str | None] | None
    no_target_directory: bool
    no_dereference: bool
    create_directories: bool
    read_only: bool
    invalid: bool


def _destination_operands(
    verb: str,
    argv: list[str],
    argv_tokens: Sequence[ArgvToken] | None,
) -> _DestinationOperands:
    """Parse the supported ln/install options and positional operands."""
    _classification = _classification_module()
    value = _classification._FlagArity.VALUE
    if verb == "ln":
        value_spec = {
            "-t": value,
            "--target-directory": value,
            "-S": value,
            "--suffix": value,
        }
        boolean_long = {
            "--backup",
            "--directory",
            "--interactive",
            "--logical",
            "--physical",
            "--relative",
            "--symbolic",
            "--force",
            "--no-dereference",
            "--no-target-directory",
            "--verbose",
        }
        boolean_short = frozenset("sfnTvbdiFLPr")
    else:
        value_spec = {
            "-t": value,
            "--target-directory": value,
            "-m": value,
            "--mode": value,
            "-o": value,
            "--owner": value,
            "-g": value,
            "--group": value,
            "-S": value,
            "--suffix": value,
        }
        boolean_long = {
            "--backup",
            "--compare",
            "--directory",
            "--preserve-timestamps",
            "--strip",
            "--no-target-directory",
            "--verbose",
        }
        boolean_short = frozenset("cCDdpsTvbZ")

    operands: list[int] = []
    target_directory: tuple[str, str | None] | None = None
    no_target_directory = False
    no_dereference = False
    directory_mode = False
    invalid = False
    read_only = False
    options = True
    index = 1
    while index < len(argv):
        token = argv[index]
        if options and token == "--":
            options = False
            index += 1
            continue
        if options and token.startswith("-") and token != "-":
            if token in {"--help", "--version"}:
                read_only = True
                index += 1
                continue
            option_value, source, next_index, recognized = (
                _classification._consume_destination_option(argv, argv_tokens, index, value_spec)
            )
            if recognized:
                option = _classification._spec_key_for_token(token, value_spec)
                if option_value is None or option_value == "--":
                    invalid = True
                elif option in {"-t", "--target-directory"}:
                    target_directory = (option_value, source)
                index = next_index
                continue
            if (
                token in boolean_long
                or token.startswith("--backup=")
                or (
                    not token.startswith("--") and all(char in boolean_short for char in token[1:])
                )
            ):
                no_target_directory |= token in {"-T", "--no-target-directory"} or (
                    not token.startswith("--") and "T" in token[1:]
                )
                no_dereference |= token in {"-n", "--no-dereference"} or (
                    not token.startswith("--") and "n" in token[1:]
                )
                directory_mode |= verb == "install" and (
                    token in {"-d", "--directory"}
                    or (not token.startswith("--") and "d" in token[1:])
                )
                index += 1
                continue
            invalid = True
            index += 1
            continue
        operands.append(index)
        index += 1
    invalid |= target_directory is not None and no_target_directory
    return _DestinationOperands(
        operands=tuple(operands),
        target_directory=target_directory,
        no_target_directory=no_target_directory,
        no_dereference=no_dereference,
        create_directories=directory_mode,
        read_only=read_only,
        invalid=invalid,
    )


def _resolved_operand(
    argv: list[str],
    index: int,
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> str | None:
    _classification = _classification_module()
    return _classification.resolve_write_target(
        argv[index],
        cwd,
        shell_source=_classification._shell_source(argv_tokens, index),
    )


def _source_basename(
    argv: list[str],
    index: int,
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> str | None:
    source = _resolved_operand(argv, index, cwd, argv_tokens)
    if source is None:
        return None
    return os.path.basename(source.rstrip(os.sep)) or None


def _directory_children(
    directory: str,
    source_indices: Sequence[int],
    argv: list[str],
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> tuple[list[str], bool]:
    targets: list[str] = []
    unresolved = False
    for index in source_indices:
        basename = _source_basename(argv, index, cwd, argv_tokens)
        if basename is None:
            unresolved = True
        else:
            targets.append(os.path.join(directory, basename))
    return targets, unresolved


def _target_directory_children(
    target_directory: tuple[str, str | None],
    source_indices: Sequence[int],
    argv: list[str],
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> tuple[list[str], bool]:
    value, source = target_directory
    directory = _classification_module().resolve_write_target(value, cwd, shell_source=source)
    if directory is None:
        return [], True
    return _directory_children(directory, source_indices, argv, cwd, argv_tokens)


def _install_targets(
    argv: list[str],
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
    options: _DestinationOperands,
) -> tuple[list[str], bool]:
    operands = options.operands
    target_directory = options.target_directory
    if options.invalid or not operands:
        return [], True
    if options.create_directories:
        if target_directory is not None:
            return [], True
        targets = [_resolved_operand(argv, index, cwd, argv_tokens) for index in operands]
        return [target for target in targets if target is not None], any(
            target is None for target in targets
        )
    if target_directory is not None:
        return _target_directory_children(target_directory, operands, argv, cwd, argv_tokens)
    if len(operands) < 2:
        return [], True

    destination = _resolved_operand(argv, operands[-1], cwd, argv_tokens)
    if destination is None:
        return [], True
    sources = operands[:-1]
    if options.no_target_directory:
        return ([destination], False) if len(sources) == 1 else ([], True)
    if len(sources) > 1 or os.path.isdir(destination):
        return _directory_children(destination, sources, argv, cwd, argv_tokens)
    return [destination], False


def _ln_entry_targets(
    argv: list[str],
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
    options: _DestinationOperands,
) -> tuple[list[str], bool]:
    operands = options.operands
    target_directory = options.target_directory
    if options.invalid or not operands:
        return [], True
    if target_directory is not None:
        return _target_directory_children(target_directory, operands, argv, cwd, argv_tokens)
    if len(operands) == 1:
        basename = _source_basename(argv, operands[0], cwd, argv_tokens)
        return ([os.path.join(cwd, basename)], False) if cwd and basename else ([], True)

    destination = _resolved_operand(argv, operands[-1], cwd, argv_tokens)
    if destination is None:
        return [], True
    sources = operands[:-1]
    if options.no_target_directory and len(sources) > 1:
        return [], True
    follows_directory = not options.no_target_directory and os.path.isdir(destination)
    if options.no_dereference and os.path.islink(destination):
        follows_directory = False
    if follows_directory or len(sources) > 1:
        return _directory_children(destination, sources, argv, cwd, argv_tokens)
    return [destination], False


def _npm_read_only_command(argv: list[str]) -> bool:
    return (
        len(argv) == 1
        or argv[1:] in (["--help"], ["--version"], ["-h"], ["-v"])
        or argv[1] in {"help", "info", "list", "ls", "prefix", "root", "show", "view"}
        or argv[1:3] in (["config", "get"], ["config", "ls"], ["config", "list"])
    )


def _npm_install_targets(
    argv: list[str],
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> tuple[list[str], bool, bool]:
    _classification = _classification_module()
    prefix_spec = {"--prefix": _classification._FlagArity.VALUE}
    separator = argv.index("--") if "--" in argv[1:] else len(argv)
    prefix: tuple[str, str | None] | None = None
    missing_prefix = False
    command_index: int | None = None
    uncertain_command = False
    index = 1
    while index < separator:
        token = argv[index]
        if token.startswith("--prefix"):
            value, source, next_index, recognized = _classification._consume_destination_option(
                argv, argv_tokens, index, prefix_spec
            )
            if not recognized:
                return [], True, True
            if value is None or value == "--":
                missing_prefix = True
            else:
                prefix = (value, source)
            index = next_index
            continue
        if command_index is None and token in {"install", "i", "add", "ci"}:
            command_index = index
        elif command_index is None and not token.startswith("-"):
            uncertain_command = True
        index += 1
    if prefix is None and not missing_prefix:
        unresolved = not _npm_read_only_command(argv)
        return [], unresolved, unresolved
    if command_index is None or uncertain_command or missing_prefix or prefix is None:
        return [], True, True
    target = _classification.resolve_write_target(prefix[0], cwd, shell_source=prefix[1])
    return ([target], False, True) if target is not None else ([], True, True)


def _ensure_scope_state(
    scopes: dict[tuple[int, ...], _ShellDirectoryState], path: tuple[int, ...]
) -> _ShellDirectoryState:
    for length in range(1, len(path) + 1):
        prefix = path[:length]
        if prefix not in scopes:
            inherited = scopes[prefix[:-1]]
            scopes[prefix] = _ShellDirectoryState(inherited.cwd, inherited.cdpath_unknown)
    return scopes[path]


def _unwrapped_prefix(prefix: list[str]) -> bool:
    """True when *prefix* contains no wrapper command — i.e. is the unwrapped shell-builtin form.

    The polarity is ``unwrapped``: returns True only when none of the wrapper
    tokens (``env``, ``sudo``, ``nice``, ``nohup``, ``timeout``, ``stdbuf``)
    appears in the prefix.
    """
    return not any(
        token in {"env", "sudo", "nice", "nohup", "timeout", "stdbuf"} for token in prefix
    )


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


def _apply_directory_command(
    verb: str,
    argv: list[str],
    argv_tokens: Sequence[ArgvToken] | None,
    state: _ShellDirectoryState,
) -> None:
    _classification = _classification_module()

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
    _classification = _classification_module()

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
            override_state = _ensure_scope_state(scopes, override_scope)
            override_state.cwd = (
                _classification.resolve_write_target(record.cwd_override, override_state.cwd) or ""
            )
            overridden_scopes.add(override_scope)
    return _ensure_scope_state(scopes, path)


def _apply_shell_builtin(
    executable: list[str],
    start: int,
    argv_tokens: Sequence[ArgvToken] | None,
    state: _ShellDirectoryState,
) -> bool:
    if argv_tokens is None or not _unwrapped_prefix(executable[:start]):
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
    _classification = _classification_module()

    if path is None or verb_cwd == state.cwd:
        return None
    if _classification._is_shell_interpreter(verb) or re.fullmatch(
        r"python(?:3(?:\.\d+)?)?", _classification._normalize_executable(verb)
    ):
        return _ShellDirectoryState(verb_cwd, state.cdpath_unknown)
    return None


def _scan_destination_command(
    verb: str,
    argv: list[str],
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> WriteTargetScan:
    options = _destination_operands(verb, argv, argv_tokens)
    if options.read_only:
        return WriteTargetScan((), False, True, False)
    if verb == "ln":
        targets, unresolved = _ln_entry_targets(argv, cwd, argv_tokens, options)
        return WriteTargetScan((), unresolved, True, True, entry_targets=tuple(targets))
    targets, unresolved = _install_targets(argv, cwd, argv_tokens, options)
    return WriteTargetScan(tuple(targets), unresolved, True, True)


def _scan_executable(
    executable: list[str],
    argv_tokens: Sequence[ArgvToken] | None,
    state: _ShellDirectoryState,
    path: tuple[int, ...] | None,
) -> tuple[WriteTargetScan, _ShellDirectoryState | None]:
    _classification = _classification_module()

    start = _classification._verb_start_index(executable)
    setting = _cdpath_setting(executable, start) if argv_tokens is not None else None
    previous_cdpath = state.cdpath_unknown
    if setting is not None:
        state.cdpath_unknown = setting[0]
    if start is None:
        return WriteTargetScan((), False, True, False), None
    handled = _apply_shell_builtin(executable, start, argv_tokens, state)
    if setting is not None and setting[1]:
        state.cdpath_unknown = previous_cdpath
    if handled or _classification.is_gh_command(executable):
        return WriteTargetScan((), False, True, False), None
    argv = executable[start:]
    verb = argv[0]
    verb_cwd = _classification._wrapped_verb_cwd(executable, start, state.cwd, argv_tokens)
    invoked = _invoked_child_state(verb, verb_cwd, state, path)
    verb_tokens = None if argv_tokens is None else argv_tokens[start:]
    verb_name = os.path.basename(verb)
    if verb_name == "npm":
        targets, unresolved, has_write = _npm_install_targets(argv, verb_cwd, verb_tokens)
        return WriteTargetScan(tuple(targets), unresolved, True, has_write), invoked
    if verb_name in {"ln", "install"}:
        return _scan_destination_command(verb_name, argv, verb_cwd, verb_tokens), invoked
    if verb_name in WRITE_VERBS:
        targets, unresolved = _classification.extract_write_verb_targets(
            verb_name, argv, verb_cwd, argv_tokens=verb_tokens
        )
        return WriteTargetScan(tuple(targets), unresolved, True, True), invoked
    if verb == "git" or verb.endswith("/git"):
        targets, unresolved, has_write = _classification._git_write_targets(
            argv, verb_cwd, executable[:start], verb_tokens
        )
        return WriteTargetScan(tuple(targets), unresolved, True, has_write), invoked
    return WriteTargetScan((), False, True, False), invoked


def scan_write_targets(command: str, cwd: str) -> WriteTargetScan:
    """Classify literal write targets and unresolved writes in evaluated shell commands."""
    _classification = _classification_module()

    records = _classification._all_evaluated_segments_with_provenance_impl(
        command, include_process_substitutions=True
    )
    if records is None:
        return WriteTargetScan((), False, False, False)

    targets: list[str] = []
    entry_targets: list[str] = []
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
        verb_scan, invoked = _scan_executable(
            executable, executable_tokens, state, record.subshell_path
        )
        targets.extend(verb_scan.targets)
        entry_targets.extend(verb_scan.entry_targets)
        targets.extend(partition.targets)
        unresolved |= verb_scan.unresolved
        has_write |= verb_scan.has_write
        if record.subshell_path is not None:
            pending_children[record.subshell_path] = (pre_command_state, invoked)
    return WriteTargetScan(
        tuple(dict.fromkeys(path for path in targets if path not in _PSEUDO_DEVICE_PATHS)),
        unresolved,
        True,
        has_write,
        tuple(dict.fromkeys(entry_targets)),
    )


if TYPE_CHECKING:
    from autoskillit.hooks._runtime import _command_classification as _classification
