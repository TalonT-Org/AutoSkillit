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
# Named groups are a non-semantic addition over the original unnamed pattern
# (`open`/`q`/`delim`/`rest` decompose the old group 1; `body` names the old
# unnamed `.*?`; `term` decomposes the old group 3/`\2` backreference). The
# matched spans are byte-identical to before -- strip_heredoc_bodies's output
# is parity-locked against core/git/bash_write_targets.py and must not change.
_HEREDOC_BODY_RE = re.compile(
    r"(?P<open><<-?\s*(?P<q>['\"]?)(?P<delim>\w+)['\"]?(?P<rest>[^\n]*))"
    r"\n(?P<body>.*?)\n\t*(?P<term>(?P=delim))(?=[ \t]*(?:\n|$))",
    re.DOTALL,
)

_HEREDOC_PLACEHOLDER_RE = re.compile(r"__AUTOSKILLIT_HEREDOC_(\d+)__")


def strip_heredoc_bodies(command: str) -> str:
    """Strip heredoc body content, preserving the opening line and terminator.

    The opening line (containing << and any real redirects) is kept intact.
    Only the body lines between the opening and terminator are removed.
    """
    return _HEREDOC_BODY_RE.sub(r"\g<open>\n\g<term>", command)


@dataclass(frozen=True, slots=True)
class StdinLiteral:
    """A heredoc or herestring body bound to the segment that consumes it.

    `kind` is `"heredoc"` or `"herestring"`. `outer_expansion` is True when
    the outer shell performs command/parameter substitution on the body
    before the consumer ever sees it -- true for an unquoted heredoc
    delimiter (`<<EOF`) or an unquoted/double-quoted herestring word, false
    when any character of the delimiter/word is quoted (`<<'EOF'`,
    `<<"EOF"`, `<<\\EOF`, a single-quoted herestring). This is independent
    of whether the *consumer* (the command the literal is piped/redirected
    into) itself executes the body as code -- see `StdinConsumer` in
    `_interpreters.py`. `source_span` is the literal's exact occurrence span
    in the original command string; `None` only for a value constructed
    directly by a test or caller rather than captured from source text.
    """

    body: str
    kind: str
    outer_expansion: bool
    source_span: tuple[int, int] | None = None


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
    stdin_literals: tuple[StdinLiteral, ...] = ()
    piped_from_previous: bool = False


def _capture_heredocs(command: str) -> tuple[str, list[StdinLiteral]]:
    """Replace each heredoc with a placeholder, returning its bound literal.

    The placeholder precedes the opening line's remainder (`rest` — real
    redirects, a pipe, `&&`, ...) so it stays in the segment that owns the
    `<<` operator even when that remainder starts a new segment once
    tokenized. `command[span]` for the returned literal's `source_span` is
    exactly the heredoc body, matching what `strip_heredoc_bodies` removes
    (both are driven by the same `_HEREDOC_BODY_RE` match).
    """
    literals: list[StdinLiteral] = []

    def _replace(match: re.Match[str]) -> str:
        index = len(literals)
        literals.append(
            StdinLiteral(
                body=match.group("body"),
                kind="heredoc",
                outer_expansion=match.group("q") == "",
                source_span=match.span("body"),
            )
        )
        return f" __AUTOSKILLIT_HEREDOC_{index}__{match.group('rest')}"

    return (_HEREDOC_BODY_RE.sub(_replace, command), literals)


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
        stripped, literals = _capture_heredocs(command)
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
    current_stdin_literals: list[StdinLiteral] = []
    piped_from_previous = False
    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]
        quoted = fully_single_quoted[index]
        raw_span = raw_spans[index]

        placeholder = _HEREDOC_PLACEHOLDER_RE.fullmatch(token)
        if placeholder is not None:
            current_stdin_literals.append(literals[int(placeholder.group(1))])
            index += 1
            continue

        if token == "<<<":
            # Standalone herestring operator: the next token is the value.
            # `outer_expansion` mirrors ArgvToken.fully_single_quoted -- only
            # a whole single-quoted word is inert to outer-shell expansion.
            if index + 1 < total:
                current_stdin_literals.append(
                    StdinLiteral(
                        body=tokens[index + 1],
                        kind="herestring",
                        outer_expansion=not fully_single_quoted[index + 1],
                    )
                )
                index += 2
            else:
                index += 1
            continue

        if len(token) > 3 and token.startswith("<<<"):
            # Fused herestring (no space before the word, e.g. `<<<'text'`):
            # shlex hands back one token whose text starts with `<<<`.
            # Provenance is re-derived past the known prefix the same way
            # `_argv_token_after_prefix` narrows a quoted suffix at the
            # ArgvToken layer -- this module cannot import that helper
            # (`_flags` sits above `_tokenizer` in the import order).
            body = token[3:]
            value_raw_span = raw_span[3:].rstrip()
            current_stdin_literals.append(
                StdinLiteral(
                    body=body,
                    kind="herestring",
                    outer_expansion=value_raw_span != f"'{body}'",
                )
            )
            index += 1
            continue

        if token in _SHELL_OPERATORS:
            if current_tokens:
                segments.append(
                    _CommandSegment(
                        current_tokens,
                        current_redirect_syntax,
                        current_argv_tokens,
                        tuple(current_stdin_literals),
                        piped_from_previous,
                    )
                )
            piped_from_previous = token == "|"
            current_tokens = []
            current_redirect_syntax = []
            current_argv_tokens = []
            current_stdin_literals = []
            index += 1
            continue

        redirect = redirects.get(token)
        restored = redirect if redirect is not None else token
        current_tokens.append(restored)
        current_redirect_syntax.append(redirect is not None)
        current_argv_tokens.append(ArgvToken(restored, quoted, raw_span))
        index += 1
    if current_tokens:
        segments.append(
            _CommandSegment(
                current_tokens,
                current_redirect_syntax,
                current_argv_tokens,
                tuple(current_stdin_literals),
                piped_from_previous,
            )
        )
    return segments


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of (verb, args...) token lists."""
    return [segment.tokens for segment in _tokenize_command_segments_with_redirects(command)]
