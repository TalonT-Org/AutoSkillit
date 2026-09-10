"""Shell tokenization primitives shared by command-inspecting hooks."""

from __future__ import annotations

import io
import re
import shlex
from dataclasses import dataclass
from typing import cast

# Operators that terminate a shlex token and split command segments.
# Parentheses are tracked by the lexer as fused tokens (`(cmd` or `cmd)`)
# and handled separately in extract_redirect_targets.
_SHELL_OPERATORS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
# Single-character shell operators that shlex.shlex(punctuation_chars=True)
# leaves sitting inside the previous token's source range. Used as the
# trailing-strip set for tokenizer span capture so a token followed
# immediately by an operator (no separating whitespace) does not appear to
# contain that operator in its raw_span.
_SHELL_OPERATOR_CHARS: str = ";|&"
_HEREDOC_BODY_RE = re.compile(
    r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?\n\t*(\2)(?=[ \t]*(?:\n|$))",
    re.DOTALL,
)

# After strip_heredoc_bodies() a heredoc collapses to "<<WORD ...\nWORD".
# This removes the marker and terminator, keeping the rest of the opening
# line (real redirects), so segments carry only executable tokens.
_HEREDOC_MARKER_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n\t*\1(?=[ \t]*(?:\n|$))")


def strip_heredoc_bodies(command: str) -> str:
    """Strip heredoc body content, preserving the opening line and terminator.

    The opening line (containing << and any real redirects) is kept intact.
    Only the body lines between the opening and terminator are removed.
    """
    return _HEREDOC_BODY_RE.sub(r"\1\n\3", command)


def _normalize_newlines_for_tokenize(command: str) -> str:
    """Make bare newlines command boundaries while preserving quoted newlines."""
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single and i + 1 < len(command):
            if command[i + 1] == "\n":
                i += 2
                continue
            result.append(c)
            result.append(command[i + 1])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\n" and not in_single and not in_double:
            result.append(" ; \n")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


@dataclass(frozen=True, slots=True)
class ArgvToken:
    """One argv token plus whether it was provably shell-inert.

    `fully_single_quoted` is True only when the token's entire source span in
    the shell command sat inside one unbroken `'...'` run -- nothing else
    (unquoted text, a second quote group) contributed to it. A substring of
    a token that was fully single-quoted end-to-end is itself still fully
    single-quoted (the quotes bounded the whole token, not part of it), so
    callers that slice `.text` (e.g. an `=`-form or bundled-short flag
    value) inherit provenance unchanged rather than re-deriving it.

    `raw_span` is the token's own rstripped source span in the shell command
    (e.g. `"'value'"` for a single-quoted token) -- kept alongside the
    coarser whole-token `fully_single_quoted` so a caller splitting `.text`
    on a *bareword* boundary it independently knows about (e.g. gh's
    `key=value` field syntax, where `key` is never itself quoted) can
    re-derive the finer-grained provenance of the part *after* that
    boundary, rather than inheriting the whole token's flag: a `key=` prefix
    sitting outside any quotes does not disqualify a separately-quoted
    value (see `_argv_token_value_after_key`).
    """

    text: str
    fully_single_quoted: bool
    raw_span: str


@dataclass(frozen=True, slots=True)
class _CommandSegment:
    tokens: list[str]
    redirect_syntax: list[bool]
    argv_tokens: list[ArgvToken]


def _mark_unquoted_output_redirects(command: str) -> tuple[str, dict[str, str]]:
    """Replace recognized redirect operators with shlex-stable placeholders."""
    rendered: list[str] = []
    redirects: dict[str, str] = {}
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single and i + 1 < len(command):
            rendered.extend((char, command[i + 1]))
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            rendered.append(char)
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            rendered.append(char)
            i += 1
            continue
        if in_single or in_double:
            rendered.append(char)
            i += 1
            continue

        start = i
        if char.isdecimal() and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|("):
            while i < len(command) and command[i].isdecimal():
                i += 1
            if i >= len(command) or command[i] != ">":
                rendered.append(command[start])
                i = start + 1
                continue
        elif char != ">":
            rendered.append(char)
            i += 1
            continue

        operator_start = start
        operator_end = i + 1
        if operator_end < len(command) and command[operator_end] == ">":
            operator_end += 1
        if operator_end < len(command) and command[operator_end] == "(":
            rendered.append(command[start])
            i = start + 1
            continue
        if (
            operator_end < len(command)
            and command[operator_end] == "&"
            and (not command[operator_start:i] or command[operator_start:i].isdecimal())
        ):
            fd_end = operator_end + 1
            while fd_end < len(command) and command[fd_end].isdecimal():
                fd_end += 1
            if fd_end == operator_end + 1:
                rendered.append(command[start])
                i = start + 1
                continue
            operator_end = fd_end

        marker = f"__AUTOSKILLIT_REDIRECT_{len(redirects)}__"
        redirects[marker] = command[operator_start:operator_end]
        rendered.extend((" ", marker, " "))
        i = operator_end
    return ("".join(rendered), redirects)


def _tokenize_command_segments_with_redirects(command: str) -> list[_CommandSegment]:
    """Tokenize commands while retaining which redirect-shaped tokens are syntax."""
    try:
        stripped = _HEREDOC_MARKER_RE.sub(r"\2", strip_heredoc_bodies(command))
        marked, redirects = _mark_unquoted_output_redirects(
            _normalize_newlines_for_tokenize(stripped)
        )
        lexer = shlex.shlex(
            marked,
            posix=True,
            punctuation_chars=";&|",
        )
        lexer.whitespace_split = True
        # Drive the lexer token-by-token (rather than `list(lexer)`) so each
        # token's own source span in *marked* can be read via instream.tell().
        # The span must be exactly the characters this lexer itself consumed
        # to produce the token, so comparing it against `'<token>'` tells us
        # whether the token was one unbroken single-quote run with nothing
        # else contributing, independent of shlex's own dequoting.
        #
        # With punctuation_chars=True, shlex does not consume operator
        # characters (`;|&`) as their own tokens until the next call -- it
        # leaves them sitting in the previous token's source range, so a
        # naive `marked[start:end]` slice captures e.g. `'foo'` followed by
        # an unseparated `;` as the single-quote token's span. Strip the
        # trailing operator chars to recover the real span; this is the
        # only place that knows about punctuation_chars' bundling quirk,
        # so it lives here rather than at every consumer. shlex.shlex(str)
        # always wraps a str instream in io.StringIO; the stub's Protocol
        # just doesn't declare .tell().
        instream = cast(io.StringIO, lexer.instream)
        tokens: list[str] = []
        fully_single_quoted: list[bool] = []
        raw_spans: list[str] = []
        while True:
            start = instream.tell()
            token = lexer.get_token()
            if token is None:
                break
            span = marked[start : instream.tell()].rstrip(_SHELL_OPERATOR_CHARS)
            tokens.append(token)
            fully_single_quoted.append(span == f"'{token}'")
            raw_spans.append(span)
    except (ValueError, TypeError):
        return []

    segments: list[_CommandSegment] = []
    current_tokens: list[str] = []
    current_redirect_syntax: list[bool] = []
    current_argv_tokens: list[ArgvToken] = []
    for token, quoted, raw_span in zip(tokens, fully_single_quoted, raw_spans, strict=True):
        if token in _SHELL_OPERATORS:
            if current_tokens:
                segments.append(
                    _CommandSegment(current_tokens, current_redirect_syntax, current_argv_tokens)
                )
                current_tokens = []
                current_redirect_syntax = []
                current_argv_tokens = []
        else:
            redirect = redirects.get(token)
            restored = redirect if redirect is not None else token
            current_tokens.append(restored)
            current_redirect_syntax.append(redirect is not None)
            current_argv_tokens.append(ArgvToken(restored, quoted, raw_span))
    if current_tokens:
        segments.append(
            _CommandSegment(current_tokens, current_redirect_syntax, current_argv_tokens)
        )
    return segments


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of (verb, args...) token lists."""
    return [segment.tokens for segment in _tokenize_command_segments_with_redirects(command)]
