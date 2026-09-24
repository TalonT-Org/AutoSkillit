"""Shell tokenization primitives shared by command-inspecting hooks."""

from __future__ import annotations

import io
import re
import shlex
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from autoskillit.hooks._classification._shell_structure import (
        _mark_grouping_delimiters,
        _mask_substitutions,
        _render_replacements,
        _skip_shell_quote,
    )
elif __package__:
    from ._shell_structure import (
        _mark_grouping_delimiters,
        _mask_substitutions,
        _render_replacements,
        _skip_shell_quote,
    )
else:
    from _shell_structure import (
        _mark_grouping_delimiters,
        _mask_substitutions,
        _render_replacements,
        _skip_shell_quote,
    )

# Operators that terminate a shlex token and split command segments.
_SHELL_OPERATORS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
# Single-character shell operators that shlex.shlex(punctuation_chars=True)
# leaves sitting inside the previous token's source range. Used as the
# trailing-strip set for tokenizer span capture so a token followed
# immediately by an operator (no separating whitespace) does not appear to
# contain that operator in its raw_span.
_SHELL_OPERATOR_CHARS: str = ";|&"
_HEREDOC_PLACEHOLDER_RE = re.compile(r"__AUTOSKILLIT_HEREDOC_(\d+)__")


def _is_case_terminator(token: str) -> bool:
    """Match shell case-pattern terminators (``;;``, ``;;;``, ...)."""
    return bool(token) and set(token) == {";"}


@dataclass(frozen=True, slots=True)
class _HeredocOpener:
    operator_span: tuple[int, int]
    delimiter: str
    delimiter_was_quoted: bool
    strip_tabs: bool


@dataclass(frozen=True, slots=True)
class _HeredocOccurrence:
    operator_span: tuple[int, int]
    body_span: tuple[int, int]
    body_deletion_span: tuple[int, int]
    capture_tail_span: tuple[int, int]
    delimiter_was_quoted: bool


def _is_comment_start(command: str, index: int, line_start: int) -> bool:
    if index == line_start:
        return True
    previous = command[index - 1]
    return previous.isspace() or previous in ";|&("


def _quoted_delimiter_fragment(command: str, start: int, line_end: int) -> tuple[str, int] | None:
    quote = command[start]
    index = start + 1
    parts: list[str] = []
    while index < line_end:
        char = command[index]
        if char == quote:
            return ("".join(parts), index + 1)
        if quote == '"' and char == "\\" and index + 1 < line_end:
            parts.append(command[index + 1])
            index += 2
        else:
            parts.append(char)
            index += 1
    return None


def _parse_heredoc_delimiter(
    command: str, start: int, line_end: int
) -> tuple[str, int, bool] | None:
    """Parse one shell word after a heredoc operator, applying quote removal."""
    parts: list[str] = []
    quoted = False
    index = start
    while index < line_end:
        char = command[index]
        if char.isspace() or char in ";|&()<>":
            break
        if char == "\\":
            if index + 1 >= line_end:
                return None
            parts.append(command[index + 1])
            quoted = True
            index += 2
            continue
        if char in "'\"":
            fragment = _quoted_delimiter_fragment(command, index, line_end)
            if fragment is None:
                return None
            text, index = fragment
            parts.append(text)
            quoted = True
            continue
        parts.append(char)
        index += 1
    return ("".join(parts), index, quoted) if parts else None


def _skip_arithmetic(command: str, start: int, line_end: int) -> int:
    depth = 1
    index = start + 3
    while index < line_end:
        if command.startswith("$((", index):
            depth += 1
            index += 3
            continue
        if command.startswith("))", index):
            depth -= 1
            index += 2
            if not depth:
                return index
            continue
        index += 2 if command[index] == "\\" and index + 1 < line_end else 1
    return line_end


def _skip_non_heredoc_syntax(command: str, index: int, line_end: int) -> int | None:
    if command[index] == "\\":
        return min(index + 2, line_end)
    if command[index] in "'\"":
        return _skip_shell_quote(command, index, line_end)
    if command.startswith("$((", index):
        return _skip_arithmetic(command, index, line_end)
    return None


def _heredoc_openers_on_line(command: str, start: int, line_end: int) -> list[_HeredocOpener]:
    openers: list[_HeredocOpener] = []
    index = start
    while index < line_end:
        char = command[index]
        skipped = _skip_non_heredoc_syntax(command, index, line_end)
        if skipped is not None:
            index = skipped
            continue
        if char == "#" and _is_comment_start(command, index, start):
            break
        if not command.startswith("<<", index):
            index += 1
            continue
        if command.startswith("<<<", index):
            index += 3
            continue

        operator_end = index + 2
        strip_tabs = operator_end < line_end and command[operator_end] == "-"
        if strip_tabs:
            operator_end += 1
        delimiter_start = operator_end
        while delimiter_start < line_end and command[delimiter_start] in " \t":
            delimiter_start += 1
        parsed = _parse_heredoc_delimiter(command, delimiter_start, line_end)
        if parsed is None:
            index = operator_end
            continue
        delimiter, delimiter_end, delimiter_was_quoted = parsed
        openers.append(
            _HeredocOpener(
                operator_span=(index, delimiter_end),
                delimiter=delimiter,
                delimiter_was_quoted=delimiter_was_quoted,
                strip_tabs=strip_tabs,
            )
        )
        index = delimiter_end
    return openers


def _heredoc_terminator(
    command: str, start: int, delimiter: str, *, strip_tabs: bool
) -> tuple[int, int, int, int] | None:
    """Return raw/rendered terminator starts, its end, and the next line start."""
    line_start = start
    while line_start < len(command):
        line_end = command.find("\n", line_start)
        content_end = len(command) if line_end < 0 else line_end
        rendered_start = line_start
        if strip_tabs:
            while rendered_start < content_end and command[rendered_start] == "\t":
                rendered_start += 1
        delimiter_end = rendered_start + len(delimiter)
        if (
            command.startswith(delimiter, rendered_start)
            and delimiter_end <= content_end
            and all(char in " \t" for char in command[delimiter_end:content_end])
        ):
            return (
                line_start,
                rendered_start,
                delimiter_end,
                content_end + 1 if line_end >= 0 else content_end,
            )
        if line_end < 0:
            break
        line_start = line_end + 1
    return None


def _heredoc_occurrences(command: str) -> list[_HeredocOccurrence]:
    """Collect heredoc bodies and spans; body contents are not parsed as commands."""
    occurrences: list[_HeredocOccurrence] = []
    position = 0
    while position < len(command):
        line_end = command.find("\n", position)
        if line_end < 0:
            break
        openers = _heredoc_openers_on_line(command, position, line_end)
        if not openers:
            position = line_end + 1
            continue

        pending: list[tuple[_HeredocOpener, tuple[int, int], tuple[int, int]]] = []
        body_start = line_end + 1
        next_position = body_start
        capture_tail_end = len(command)
        for opener in openers:
            terminator = _heredoc_terminator(
                command, body_start, opener.delimiter, strip_tabs=opener.strip_tabs
            )
            if terminator is None:
                body_end = len(command)
                if body_end > body_start and command.endswith("\n"):
                    body_end -= 1
                pending.append(
                    (
                        opener,
                        (body_start, body_end),
                        (body_start, len(command)),
                    )
                )
                capture_tail_end = len(command)
                next_position = len(command)
                break

            raw_start, rendered_start, delimiter_end, next_position = terminator
            body_end = raw_start
            if body_end > body_start and command[body_end - 1] == "\n":
                body_end -= 1
            pending.append(
                (
                    opener,
                    (body_start, body_end),
                    (body_start, rendered_start),
                )
            )
            body_start = next_position
            capture_tail_end = delimiter_end

        capture_tail_span = (line_end, capture_tail_end)
        occurrences.extend(
            _HeredocOccurrence(
                operator_span=opener.operator_span,
                body_span=body_span,
                body_deletion_span=body_deletion_span,
                capture_tail_span=capture_tail_span,
                delimiter_was_quoted=opener.delimiter_was_quoted,
            )
            for opener, body_span, body_deletion_span in pending
        )
        position = next_position
    return occurrences


def strip_heredoc_bodies(command: str) -> str:
    """Strip heredoc body content, preserving the opening line and terminator.

    The opening line (containing << and any real redirects) is kept intact.
    Only the body lines between the opening and terminator are removed.
    """
    return _render_replacements(
        command,
        [(*occurrence.body_deletion_span, "") for occurrence in _heredoc_occurrences(command)],
    )


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
    `feeds_stdin` identifies the redirect that supplies the segment's stdin;
    earlier redirects remain available for outer-expansion analysis. It defaults
    to True so directly constructed literals retain the historical behavior.
    """

    text: str
    kind: str
    outer_expansion: bool
    source_span: tuple[int, int] | None = None
    feeds_stdin: bool = True


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
    function_body: bool = False
    subshell_path: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluatedSegment:
    """An evaluated argv segment and its submitted-command provenance.

    ``provenance`` is absent for commands recovered from evaluated payloads,
    whose tokens have no source span in the submitted command text.
    """

    tokens: list[str]
    provenance: _CommandSegment | None

    def __post_init__(self) -> None:
        if not self.tokens:
            raise ValueError("EvaluatedSegment requires a non-empty token list")
        if self.provenance is not None and self.provenance.tokens != self.tokens:
            raise ValueError(
                "EvaluatedSegment tokens must match provenance.tokens when provenance is set"
            )

    @property
    def subshell_path(self) -> tuple[int, ...]:
        """Subshell nesting path of the owning submitted segment (empty for payload segments)."""
        return self.provenance.subshell_path if self.provenance is not None else ()


def _capture_heredocs(command: str) -> tuple[str, list[StdinLiteral]]:
    """Replace each `<<EOF` heredoc with a placeholder; return the bound literals."""
    occurrences = _heredoc_occurrences(command)
    literals = [
        StdinLiteral(
            text=command[occurrence.body_span[0] : occurrence.body_span[1]],
            kind="heredoc",
            outer_expansion=not occurrence.delimiter_was_quoted,
            source_span=occurrence.body_span,
        )
        for occurrence in occurrences
    ]
    replacements = [
        (*occurrence.operator_span, f" __AUTOSKILLIT_HEREDOC_{index}__")
        for index, occurrence in enumerate(occurrences)
    ]
    replacements.extend(
        (*span, "") for span in {occurrence.capture_tail_span for occurrence in occurrences}
    )
    return (_render_replacements(command, replacements), literals)


def _finalize_stdin_literals(literals: list[StdinLiteral]) -> tuple[StdinLiteral, ...]:
    return tuple(
        replace(literal, feeds_stdin=index == len(literals) - 1)
        for index, literal in enumerate(literals)
    )


def _output_redirect_end(command: str, start: int) -> int | None:
    i = start
    char = command[i]
    if char.isdecimal() and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|("):
        while i < len(command) and command[i].isdecimal():
            i += 1
        if i >= len(command) or command[i] != ">":
            return None
    elif char != ">":
        return None

    operator_end = i + 1
    if operator_end < len(command) and command[operator_end] == ">":
        operator_end += 1
    if operator_end < len(command) and command[operator_end] == "(":
        return None
    if (
        operator_end < len(command)
        and command[operator_end] == "&"
        and (not command[start:i] or command[start:i].isdecimal())
    ):
        fd_end = operator_end + 1
        while fd_end < len(command) and command[fd_end].isdecimal():
            fd_end += 1
        if fd_end == operator_end + 1:
            return None
        operator_end = fd_end
    return operator_end


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

        operator_end = _output_redirect_end(command, i)
        if operator_end is None:
            rendered.append(char)
            i += 1
            continue

        marker = f"__AUTOSKILLIT_REDIRECT_{len(redirects)}__"
        redirects[marker] = command[i:operator_end]
        rendered.extend((" ", marker, " "))
        i = operator_end
    return ("".join(rendered), redirects)


_LexedCommand = tuple[
    list[str],
    list[bool],
    list[str],
    list[StdinLiteral],
    dict[str, str],
    dict[str, str],
    dict[str, tuple[str, int]],
]


def _lex_command(command: str) -> _LexedCommand | None:
    try:
        stripped, literals = _capture_heredocs(command)
        masked = _mask_substitutions(stripped)
        if masked is None:
            return None
        substitutions_marked, substitutions = masked
        redirects_marked, redirects = _mark_unquoted_output_redirects(
            _normalize_newlines_for_tokenize(substitutions_marked)
        )
        grouped = _mark_grouping_delimiters(redirects_marked)
        if grouped is None:
            return None
        marked, groups = grouped
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
        return None

    return tokens, fully_single_quoted, raw_spans, literals, redirects, substitutions, groups


def _herestring_at(
    tokens: list[str], quoted: list[bool], raw_spans: list[str], index: int
) -> tuple[StdinLiteral | None, int] | None:
    """Match a herestring operator at *index*.

    Returns ``None`` when the token at *index* is not a herestring. When it
    is, returns ``(literal, next_index)``: ``literal`` is ``None`` if the
    operator was the trailing ``<<<`` with no body, otherwise it is the
    captured body. ``next_index`` is the index of the first token after the
    herestring.
    """
    token = tokens[index]
    if token == "<<<":
        if index + 1 < len(tokens):
            return (
                StdinLiteral(tokens[index + 1], "herestring", not quoted[index + 1]),
                index + 2,
            )
        return None, index + 1
    if len(token) > 3 and token.startswith("<<<"):
        body = token[3:]
        value_raw_span = raw_spans[index][3:].rstrip()
        return StdinLiteral(body, "herestring", value_raw_span != f"'{body}'"), index + 1
    return None


def _restore_substitutions(
    token: str, raw_span: str, quoted: bool, substitutions: dict[str, str]
) -> tuple[str, str, bool]:
    for marker, original in substitutions.items():
        raw_span = raw_span.replace(marker, original)
        if marker in token:
            token = token.replace(marker, original)
            quoted = False
    return token, raw_span, quoted


def _advance_group_context(
    kind: str,
    group_id: int,
    subshell_path: tuple[int, ...],
    function_depth: int,
    brace_functions: list[bool],
) -> tuple[tuple[int, ...], int]:
    if kind == "open_subshell":
        subshell_path += (group_id,)
    elif kind == "close_subshell":
        assert subshell_path, "close_subshell emitted without matching open_subshell"
        subshell_path = subshell_path[:-1]
    elif kind in {"open_brace", "open_function"}:
        brace_functions.append(kind == "open_function")
        function_depth += kind == "open_function"
    elif kind == "close_brace":
        assert brace_functions, "close_brace emitted without matching open_brace/open_function"
        function_depth -= brace_functions.pop()
    return subshell_path, function_depth


def _tokenize_command_segments_with_redirects(command: str) -> list[_CommandSegment] | None:
    """Tokenize commands while retaining which redirect-shaped tokens are syntax."""
    lexed = _lex_command(command)
    if lexed is None:
        return None
    tokens, fully_single_quoted, raw_spans, literals, redirects, substitutions, groups = lexed

    segments: list[_CommandSegment] = []
    current_tokens: list[str] = []
    current_redirect_syntax: list[bool] = []
    current_argv_tokens: list[ArgvToken] = []
    current_stdin_literals: list[StdinLiteral] = []
    piped_from_previous = False
    subshell_path: tuple[int, ...] = ()
    function_depth = 0
    brace_functions: list[bool] = []

    def flush() -> None:
        nonlocal current_tokens, current_redirect_syntax, current_argv_tokens
        nonlocal current_stdin_literals
        if (
            current_tokens
            and not (
                len(current_tokens) == 1
                and current_tokens[0] in {"if", "while", "until", "then", "else", "elif", "do"}
            )
            and not (
                len(current_tokens) == 1
                and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\(\)", current_tokens[0])
            )
            and not (len(current_tokens) == 2 and current_tokens[0] == "function")
        ):
            segments.append(
                _CommandSegment(
                    current_tokens,
                    current_redirect_syntax,
                    current_argv_tokens,
                    _finalize_stdin_literals(current_stdin_literals),
                    piped_from_previous,
                    function_body=function_depth > 0,
                    subshell_path=subshell_path,
                )
            )
        current_tokens = []
        current_redirect_syntax = []
        current_argv_tokens = []
        current_stdin_literals = []

    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]
        quoted = fully_single_quoted[index]
        raw_span = raw_spans[index]

        group = groups.get(token)
        if group is not None:
            flush()
            kind, group_id = group
            subshell_path, function_depth = _advance_group_context(
                kind, group_id, subshell_path, function_depth, brace_functions
            )
            piped_from_previous = False
            index += 1
            continue

        placeholder = _HEREDOC_PLACEHOLDER_RE.fullmatch(token)
        if placeholder is not None:
            current_stdin_literals.append(literals[int(placeholder.group(1))])
            index += 1
            continue

        herestring = _herestring_at(tokens, fully_single_quoted, raw_spans, index)
        if herestring is not None:
            literal, index = herestring
            if literal is not None:
                current_stdin_literals.append(literal)
            continue

        if token in _SHELL_OPERATORS or _is_case_terminator(token):
            flush()
            piped_from_previous = token == "|"
            index += 1
            continue

        redirect = redirects.get(token)
        restored = redirect if redirect is not None else token
        restored, raw_span, quoted = _restore_substitutions(
            restored, raw_span, quoted, substitutions
        )
        current_tokens.append(restored)
        current_redirect_syntax.append(redirect is not None)
        current_argv_tokens.append(ArgvToken(restored, quoted, raw_span))
        index += 1
    flush()
    return segments


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of (verb, args...) token lists."""
    return [segment.tokens for segment in _tokenize_command_segments_with_redirects(command) or []]
