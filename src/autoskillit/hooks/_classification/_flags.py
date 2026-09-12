"""Flag parsing and protected-read classification for hook commands."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._runtime._command_classification import (
        ArgvToken,
        SearchPattern,
        _command_start_index,
        _normalize_newlines_for_tokenize,
        command_verb,
        extract_git_subcommand_and_flags,
    )


# Moved from _command_classification.py (rectify #4941 Part A) to keep that
# facade under REQ-CNST-010's line cap; this module is their sole consumer.
# Re-exported through the facade's existing block B bootstrap.
_PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"add", "diff", "status"})

_GIT_ADD_CONTENT_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "--patch",
        "-e",
        "--edit",
        "-i",
        "--interactive",
        "--pathspec-from-file",
        # Content-staging flags: -A/--all stages all changes (incl. content);
        # --force/--no-ignore-removal/--no-all are the no-restriction variants.
        # Without these, `git add -A -- src/.../foo.yaml` is classified as
        # metadata but actually stages content for indirect read via
        # `git diff --staged`.
        "-A",
        "--all",
        "--force",
        "--no-ignore-removal",
        "--no-all",
    }
)
_GIT_STATUS_CONTENT_FLAGS: frozenset[str] = frozenset({"-v", "--verbose"})
_GIT_DIFF_CONTENT_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "--patch",
        "--patch-with-stat",
        "--patch-with-raw",
        "--binary",
        "--text",
        "--word-diff",
        "--color-words",
    }
)
_GIT_DIFF_METADATA_FLAGS: frozenset[str] = frozenset(
    {
        "--name-only",
        "--name-status",
        "--stat",
        "--shortstat",
        "--numstat",
        "--summary",
    }
)
_SHELL_SUBSTITUTION_RE = re.compile(r"\$\(|`|[<>]\(")
_SHELL_STATE_VAR_RE = re.compile(r"\$(?:_|[A-Za-z][A-Za-z0-9_]*|\{[^}]+\})")
_PROTECTED_READ_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
_WC_FLAG_RE = re.compile(r"-l+|--lines$")


_GIT_GLOBAL_FLAGS: frozenset[str] = frozenset(
    {"-C", "--work-tree", "--git-dir", "--no-pager", "--bare", "-c"}
)
_GIT_GLOBAL_FLAGS_WITH_VALUE: frozenset[str] = frozenset({"-C", "--work-tree", "--git-dir", "-c"})


class _FlagArity(StrEnum):
    """Per-flag arity classification used by every {flag: arity} spec table.

    BOOLEAN — the flag takes no value; the next token is its own argument.
    VALUE — the flag takes exactly one value in the next token (or joined via
    `=` for long forms, or glued onto a short form like -XPOST).

    A StrEnum, not a plain Enum: this module can be loaded under the dotted
    `autoskillit.hooks._classification._flags` package name and the bare
    `_classification._flags` standalone name. The two loads produce distinct
    `_FlagArity` class objects, so an `is`
    comparison between a value sourced from one and `_FlagArity.VALUE`
    sourced from the other silently fails even though both represent the
    same arity. StrEnum members compare equal by their underlying str value
    across class identities (`A.VALUE == B.VALUE` is True even when `A is
    not B`), so every comparison against `_FlagArity.VALUE`/`.BOOLEAN`
    anywhere in the codebase must use `==`, never `is`.
    """

    BOOLEAN = auto()
    VALUE = auto()


def _argv_token_after_prefix(token: ArgvToken, prefix: str, value_text: str) -> ArgvToken:
    """Return a suffix token with quote provenance narrowed past *prefix*.

    Fully single-quoted tokens inherit their provenance. Otherwise, only an
    exact single-quoted raw value is marked safe; unusual prefix quoting may
    conservatively produce False but cannot create a false-safe result.
    """
    if token.fully_single_quoted:
        return ArgvToken(value_text, True, token.raw_span)
    value_raw_span = token.raw_span[len(prefix) :].rstrip()
    return ArgvToken(value_text, value_raw_span == f"'{value_text}'", value_raw_span)


def _argv_token_value_after_key(token: ArgvToken, key: str) -> ArgvToken:
    """Split `token.text` on its first '=' (with `key` already known to be

    the part before it, e.g. from `token.text.partition("=")`) -- gh's
    `-f`/`-F key=value` field syntax, where `key` is a bareword the caller
    already knows. See `_argv_token_after_prefix` for the provenance rule.
    """
    _, _, value_text = token.text.partition("=")
    return _argv_token_after_prefix(token, f"{key}=", value_text)


def _consume_argv_flag(
    tokens: Sequence[ArgvToken], i: int, spec: Mapping[str, _FlagArity]
) -> tuple[ArgvToken | None, int, bool]:
    """Consume the flag at tokens[i] against *spec*.

    Returns (value_or_None, next_index, recognized). Handles the same three
    forms _flag_value already supports for a single named flag -- space
    (`--flag value`), `=`-joined long form (`--flag=value`), and bundled
    short form (`-Xvalue`) -- but spec-driven across every flag in *spec* at
    once, and CLI-agnostic: any consumer with its own {flag: arity} spec
    table (gh api, curl, git's global flags, pip's global flags) shares this
    one engine rather than hand-rolling its own argv-walking loop. If the
    token at i is not '-'-prefixed, or not in spec (in any of its
    recognized forms), recognized=False and next_index==i -- the caller
    decides how to handle an unresolved token.
    """
    token = tokens[i]
    if not token.text.startswith("-"):
        return (None, i, False)

    arity = spec.get(token.text)
    if arity == _FlagArity.BOOLEAN:
        return (None, i + 1, True)
    if arity == _FlagArity.VALUE:
        if i + 1 >= len(tokens):
            return (None, i + 1, True)
        return (tokens[i + 1], i + 2, True)

    if token.text.startswith("--") and "=" in token.text:
        long_flag, _, value = token.text.partition("=")
        if spec.get(long_flag) == _FlagArity.VALUE:
            return (
                _argv_token_after_prefix(token, f"{long_flag}=", value),
                i + 1,
                True,
            )

    for flag, flag_arity in spec.items():
        if (
            flag_arity == _FlagArity.VALUE
            and len(flag) == 2
            and not flag.startswith("--")
            and token.text.startswith(flag)
            and token.text != flag
        ):
            value_text = token.text[len(flag) :]
            return (
                _argv_token_after_prefix(token, flag, value_text),
                i + 1,
                True,
            )

    return (None, i, False)


def _consume_str_flag(
    tokens: Sequence[str], i: int, spec: Mapping[str, _FlagArity]
) -> tuple[str | None, int, bool]:
    """String-only convenience wrapper around _consume_argv_flag for consumers

    (git global-flag skipping, curl's non-dynamic-value-checked flags, pip's
    global flags) that only need flag recognition/arity to correctly skip
    past a flag (and its value, if any) -- not ArgvToken's quote-provenance
    tracking, since these values never feed a dynamic-value check. Delegates
    the actual algorithm entirely to _consume_argv_flag rather than
    re-implementing it for plain strings.
    """
    argv_tokens = [ArgvToken(t, False, t) for t in tokens]
    value, next_i, recognized = _consume_argv_flag(argv_tokens, i, spec)
    return (value.text if value is not None else None, next_i, recognized)


# Complete git global-flag allowlist, verified against a live `git --help`
# read (its usage synopsis is git's own authoritative, exhaustive list of
# top-level global options: `git [-v | --version] [-h | --help] [-C <path>]
# [-c <name>=<value>] [--exec-path[=<path>]] [--html-path] [--man-path]
# [--info-path] [-p | --paginate | -P | --no-pager] [--no-replace-objects]
# [--bare] [--git-dir=<path>] [--work-tree=<path>] [--namespace=<name>]
# [--config-env=<name>=<envvar>] <command> [<args>]`). Sourced from
# _GIT_GLOBAL_FLAGS/_GIT_GLOBAL_FLAGS_WITH_VALUE above rather than
# re-entering their members with a possibly-conflicting arity: 4 of
# _GIT_GLOBAL_FLAGS's 6 members duplicate _GIT_GLOBAL_FLAGS_WITH_VALUE's
# value-taking flags -- only --no-pager/--bare are genuinely boolean.
_GIT_GLOBAL_FLAG_SPEC: Mapping[str, _FlagArity] = {
    **{flag: _FlagArity.VALUE for flag in _GIT_GLOBAL_FLAGS_WITH_VALUE},
    **{flag: _FlagArity.BOOLEAN for flag in (_GIT_GLOBAL_FLAGS - _GIT_GLOBAL_FLAGS_WITH_VALUE)},
    "--namespace": _FlagArity.VALUE,
    "--config-env": _FlagArity.VALUE,
    # --exec-path[=<path>] is optional-value (usable bare, or with `=`); this
    # module's binary arity model can't express "optional". BOOLEAN is the
    # correct default for its common bare usage; the rare `=`-form
    # invocation instead fails closed via the unrecognized-flag sentinel
    # rather than being silently misparsed -- an acceptable trade-off for a
    # flag that in practice is essentially never used in automation.
    "--exec-path": _FlagArity.BOOLEAN,
    "--html-path": _FlagArity.BOOLEAN,
    "--man-path": _FlagArity.BOOLEAN,
    "--info-path": _FlagArity.BOOLEAN,
    "-p": _FlagArity.BOOLEAN,
    "--paginate": _FlagArity.BOOLEAN,
    "-P": _FlagArity.BOOLEAN,
    "--no-replace-objects": _FlagArity.BOOLEAN,
    "-v": _FlagArity.BOOLEAN,
    "--version": _FlagArity.BOOLEAN,
    "-h": _FlagArity.BOOLEAN,
    "--help": _FlagArity.BOOLEAN,
}

# pip global-flag spec (flags accepted before the `install` subcommand),
# verified against a live `pip --help` read. Covers the flags named by this
# rectify's investigation (--index-url, --proxy, --retries, --timeout,
# --cache-dir, --log) plus the pre-existing -r/-c/-t/-b/--requirement/
# --constraint set _find_pip_install already recognized, plus pip's other
# common general options, to correctly skip past a global flag (and its
# value) to find the `install` token. Imported by unsafe_install_guard.py's
# _find_pip_install helper.
_PIP_GLOBAL_FLAG_SPEC: Mapping[str, _FlagArity] = {
    "-r": _FlagArity.VALUE,
    "--requirement": _FlagArity.VALUE,
    "-c": _FlagArity.VALUE,
    "--constraint": _FlagArity.VALUE,
    "-t": _FlagArity.VALUE,
    "-b": _FlagArity.VALUE,
    "--index-url": _FlagArity.VALUE,
    "--proxy": _FlagArity.VALUE,
    "--retries": _FlagArity.VALUE,
    "--timeout": _FlagArity.VALUE,
    "--cache-dir": _FlagArity.VALUE,
    "--log": _FlagArity.VALUE,
    "--python": _FlagArity.VALUE,
    "--keyring-provider": _FlagArity.VALUE,
    "--exists-action": _FlagArity.VALUE,
    "--trusted-host": _FlagArity.VALUE,
    "--cert": _FlagArity.VALUE,
    "--client-cert": _FlagArity.VALUE,
    "--use-feature": _FlagArity.VALUE,
    "--use-deprecated": _FlagArity.VALUE,
    "--resume-retries": _FlagArity.VALUE,
    "-h": _FlagArity.BOOLEAN,
    "--help": _FlagArity.BOOLEAN,
    "--debug": _FlagArity.BOOLEAN,
    "--isolated": _FlagArity.BOOLEAN,
    "--require-virtualenv": _FlagArity.BOOLEAN,
    "-v": _FlagArity.BOOLEAN,
    "--verbose": _FlagArity.BOOLEAN,
    "-V": _FlagArity.BOOLEAN,
    "--version": _FlagArity.BOOLEAN,
    "-q": _FlagArity.BOOLEAN,
    "--quiet": _FlagArity.BOOLEAN,
    "--no-input": _FlagArity.BOOLEAN,
    "--no-cache-dir": _FlagArity.BOOLEAN,
    "--disable-pip-version-check": _FlagArity.BOOLEAN,
    "--no-color": _FlagArity.BOOLEAN,
}


def _is_allowed_wc_flag(token: str) -> bool:
    """Return True when *token* is a wc flag that does not reveal file contents.

    Allows ``-l``, repeated ``-l`` (e.g. ``-ll``), and the long form ``--lines``
    only. Any value-bearing variant (``--lines=10``) or compound form
    (``-lL``) is rejected because those are not used for metadata-only reads.
    """
    return bool(_WC_FLAG_RE.fullmatch(token))


def is_allowed_protected_path_metadata_command(segment: list[str]) -> bool:
    """Return True for protected-path commands that inspect metadata or VCS state.

    Protected recipe/skill/agent paths are normally deny-by-default because most
    commands that mention them are content reads. These narrow exceptions support
    legitimate pipeline work on files already in scope.
    """
    verb = command_verb(segment)
    if verb == "git" or verb.endswith("/git"):
        git_parts = extract_git_subcommand_and_flags(segment)
        if git_parts is None:
            return False
        subcommand, flags = git_parts
        if subcommand not in _PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS:
            return False
        if subcommand == "add":
            return not any(
                flag in _GIT_ADD_CONTENT_FLAGS or flag.startswith("--pathspec-from-file=")
                for flag in flags
            )
        if subcommand == "status":
            return not any(flag in _GIT_STATUS_CONTENT_FLAGS for flag in flags)
        if subcommand == "diff":
            if any(
                flag in _GIT_DIFF_CONTENT_FLAGS
                or flag.startswith("-U")
                or flag.startswith("--unified")
                or flag.startswith("--word-diff")
                or flag.startswith("--color-words")
                or flag.startswith("--patch-with-stat")
                or flag.startswith("--patch-with-raw")
                for flag in flags
            ):
                return False
            return any(
                flag in _GIT_DIFF_METADATA_FLAGS or flag.startswith("--stat=") for flag in flags
            )
        return False
    if verb == "wc" or verb.endswith("/wc"):
        start = _command_start_index(segment)
        if start is None:
            return False
        flags = [token for token in segment[start + 1 :] if token.startswith("-")]
        return bool(flags) and all(_is_allowed_wc_flag(token) for token in flags)
    return False


def _tokenize_protected_read_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(
            _normalize_newlines_for_tokenize(command), posix=True, punctuation_chars=";&|()"
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except (ValueError, TypeError):
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _PROTECTED_READ_SHELL_OPS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def command_has_blocked_protected_path_read(
    command: str, protected_path_patterns: Sequence[SearchPattern]
) -> bool:
    """Return True when a command reads a protected recipe/skill/agent path."""
    if not any(pattern.search(command) for pattern in protected_path_patterns):
        return False

    if "<<" in command or _SHELL_SUBSTITUTION_RE.search(command):
        return True

    segments = _tokenize_protected_read_segments(command)
    if not segments:
        return True

    if len(segments) > 1 and _SHELL_STATE_VAR_RE.search(command):
        return True

    for segment in segments:
        segment_text = " ".join(segment)
        if any(pattern.search(segment_text) for pattern in protected_path_patterns):
            if not is_allowed_protected_path_metadata_command(segment):
                return True
    return False


if not TYPE_CHECKING:
    if __package__ == "autoskillit.hooks._classification":
        from .._runtime import _command_classification as _classification
    else:
        import _command_classification as _classification

    ArgvToken = _classification.ArgvToken
    SearchPattern = _classification.SearchPattern
    _command_start_index = _classification._command_start_index
    _normalize_newlines_for_tokenize = _classification._normalize_newlines_for_tokenize
    command_verb = _classification.command_verb
    extract_git_subcommand_and_flags = _classification.extract_git_subcommand_and_flags
