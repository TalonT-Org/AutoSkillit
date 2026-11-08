"""Scope-aware write-target scan for evaluated shell and Python commands."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
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


if TYPE_CHECKING:
    from autoskillit.hooks._runtime import _shell_segment_walk
else:
    if __package__:
        from . import _shell_segment_walk
    else:
        import _shell_segment_walk

_classification_module = _shell_segment_walk._classification_module


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


def _scan_verb(seg: _shell_segment_walk.WalkedSegment) -> WriteTargetScan:
    _classification = _classification_module()

    if seg.start is None or seg.handled_builtin or _classification.is_gh_command(seg.executable):
        return WriteTargetScan((), False, True, False)
    argv = seg.executable[seg.start :]
    verb = argv[0]
    verb_tokens = None if seg.executable_tokens is None else seg.executable_tokens[seg.start :]
    verb_name = os.path.basename(verb)
    if verb_name == "npm":
        targets, unresolved, has_write = _npm_install_targets(argv, seg.verb_cwd, verb_tokens)
        return WriteTargetScan(tuple(targets), unresolved, True, has_write)
    if verb_name in {"ln", "install"}:
        return _scan_destination_command(verb_name, argv, seg.verb_cwd, verb_tokens)
    if verb_name in WRITE_VERBS:
        targets, unresolved = _classification.extract_write_verb_targets(
            verb_name, argv, seg.verb_cwd, argv_tokens=verb_tokens
        )
        return WriteTargetScan(tuple(targets), unresolved, True, True)
    if verb == "git" or verb.endswith("/git"):
        targets, unresolved, has_write = _classification._git_write_targets(
            argv, seg.verb_cwd, seg.executable[: seg.start], verb_tokens
        )
        return WriteTargetScan(tuple(targets), unresolved, True, has_write)
    return WriteTargetScan((), False, True, False)


def scan_write_targets(command: str, cwd: str) -> WriteTargetScan:
    """Classify literal write targets and unresolved writes in evaluated shell commands."""
    walk = _shell_segment_walk.walk_evaluated_segments(command, cwd)
    if walk is None:
        return WriteTargetScan((), False, False, False)

    targets: list[str] = []
    entry_targets: list[str] = []
    unresolved = False
    has_write = False
    for seg in walk:
        unresolved |= seg.partition.unresolved
        has_write |= bool(seg.partition.file_redirect_count)
        verb_scan = _scan_verb(seg)
        targets.extend(verb_scan.targets)
        entry_targets.extend(verb_scan.entry_targets)
        targets.extend(seg.partition.targets)
        unresolved |= verb_scan.unresolved
        has_write |= verb_scan.has_write
    return WriteTargetScan(
        tuple(dict.fromkeys(path for path in targets if path not in _PSEUDO_DEVICE_PATHS)),
        unresolved,
        True,
        has_write,
        tuple(dict.fromkeys(entry_targets)),
    )
