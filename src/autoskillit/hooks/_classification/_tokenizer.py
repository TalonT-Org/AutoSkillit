"""Shell tokenization primitives shared by command-inspecting hooks."""

from __future__ import annotations

import io
import re
import shlex
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._shell_structure import (
        _mark_grouping_delimiters,
        _mark_unquoted_output_redirects,
        _mask_substitutions,
        _normalize_newlines_for_tokenize,
        _render_replacements,
        _skip_shell_quote,
    )
    from autoskillit.hooks._classification._source_map import SourceMappedText
elif __package__:
    from . import _source_map
    from ._shell_structure import (
        _mark_grouping_delimiters,
        _mark_unquoted_output_redirects,
        _mask_substitutions,
        _normalize_newlines_for_tokenize,
        _render_replacements,
        _skip_shell_quote,
    )

    SourceMappedText = _source_map.SourceMappedText
else:
    import _source_map
    from _shell_structure import (
        _mark_grouping_delimiters,
        _mark_unquoted_output_redirects,
        _mask_substitutions,
        _normalize_newlines_for_tokenize,
        _render_replacements,
        _skip_shell_quote,
    )

    SourceMappedText = _source_map.SourceMappedText

# Operators that terminate a shlex token and split command segments.
_SHELL_OPERATORS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
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


def _strip_heredoc_bodies_mapped(command: str) -> SourceMappedText:
    return _render_replacements(
        SourceMappedText.identity(command),
        [(*occurrence.body_deletion_span, "") for occurrence in _heredoc_occurrences(command)],
    )


def strip_heredoc_bodies(command: str) -> str:
    """Strip heredoc body content, preserving the opening line and terminator.

    The opening line (containing << and any real redirects) is kept intact.
    Only the body lines between the opening and terminator are removed.
    """
    return _strip_heredoc_bodies_mapped(command).text


@dataclass(frozen=True, slots=True)
class StdinLiteral:
    """A heredoc or herestring body bound to the segment that consumes it.

    `kind` is `"heredoc"` or `"herestring"`. `outer_expansion` is True when
    the outer shell performs command/parameter substitution on the body
    before the consumer ever sees it -- true for an unquoted heredoc
    delimiter (`<<EOF`) or an unquoted/double-quoted herestring word, false
    when any character of the delimiter is quoted (`<<'EOF'`,
    `<<"EOF"`, `<<\\EOF`) or the entire herestring word is single-quoted. This is independent
    of whether the *consumer* (the command the literal is piped/redirected
    into) itself executes the body as code -- see `StdinConsumer` in
    `_interpreters.py`. `source_span` covers a heredoc's body (equal to `text`),
    or a herestring's word as written, including quotes, whose dequoted value
    is `text`. Coordinates refer to the original command; `None` only for
    directly constructed values.
    `feeds_stdin` identifies the redirect that supplies the segment's stdin;
    earlier redirects remain available for outer-expansion analysis. It defaults
    to True so directly constructed literals retain the historical behavior.
    """

    text: str
    kind: str
    outer_expansion: bool
    source_span: tuple[int, int] | None = None
    feeds_stdin: bool = True


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

    `raw_span` is the exact source span of the token in the tokenizer's marked text
    (substitution markers restored)
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
    """An evaluated argv segment with token and process-scope provenance.

    ``provenance`` is absent for commands recovered from evaluated payloads,
    whose tokens have no source span in the submitted command text. ``argv_tokens``
    is absent only for literal Python subprocess argv, which was not shell-lexed.
    ``subshell_path`` is None when the payload's owning segment is unknown.
    ``cwd_override`` is a Python subprocess's explicitly configured initial cwd.
    """

    tokens: list[str]
    provenance: _CommandSegment | None
    redirect_syntax: list[bool]
    argv_tokens: list[ArgvToken] | None
    subshell_path: tuple[int, ...] | None
    cwd_override: str | None = None

    def __post_init__(self) -> None:
        if not self.tokens:
            raise ValueError("EvaluatedSegment requires a non-empty token list")
        if len(self.redirect_syntax) != len(self.tokens):
            raise ValueError("EvaluatedSegment redirect_syntax must align with tokens")
        if self.argv_tokens is not None and len(self.argv_tokens) != len(self.tokens):
            raise ValueError("EvaluatedSegment argv_tokens must align with tokens")
        if self.provenance is not None:
            if self.provenance.tokens != self.tokens:
                raise ValueError(
                    "EvaluatedSegment tokens must match provenance.tokens when provenance is set"
                )
            if self.provenance.redirect_syntax != self.redirect_syntax:
                raise ValueError(
                    "EvaluatedSegment redirect_syntax must match provenance.redirect_syntax "
                    "when provenance is set"
                )
            if self.provenance.argv_tokens != self.argv_tokens:
                raise ValueError(
                    "EvaluatedSegment argv_tokens must match provenance.argv_tokens "
                    "when provenance is set"
                )
            if self.provenance.subshell_path != self.subshell_path:
                raise ValueError(
                    "EvaluatedSegment subshell_path must match provenance.subshell_path "
                    "when provenance is set"
                )


def _capture_heredocs(command: str) -> tuple[SourceMappedText, list[StdinLiteral]]:
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
    mapped = _render_replacements(SourceMappedText.identity(command), replacements)
    return mapped, literals


def _finalize_stdin_literals(literals: list[StdinLiteral]) -> tuple[StdinLiteral, ...]:
    return tuple(
        replace(literal, feeds_stdin=index == len(literals) - 1)
        for index, literal in enumerate(literals)
    )


class _LexStream:
    """Marked text for shlex that records discarded comments and reads of EOF."""

    def __init__(self, text: str) -> None:
        self._buffer = io.StringIO(text)
        self.comment_starts: list[int] = []
        self.hit_eof = False

    def read(self, size: int, /) -> str:
        chunk = self._buffer.read(size)
        if not chunk:
            self.hit_eof = True
        return chunk

    def readline(self) -> str:
        self.comment_starts.append(self._buffer.tell() - 1)
        return self._buffer.readline()

    def close(self) -> None:
        self._buffer.close()

    def tell(self) -> int:
        return self._buffer.tell()


def _skip_lexer_trivia(marked: str, start: int, lexer: shlex.shlex) -> int:
    while start < len(marked):
        if marked[start] in lexer.whitespace:
            start += 1
        elif marked[start] in lexer.commenters:
            newline = marked.find("\n", start)
            start = len(marked) if newline < 0 else newline + 1
        else:
            break
    return start


def _token_end(
    marked: str, stream: _LexStream, seen: int, start: int, lexer: shlex.shlex
) -> tuple[int, int]:
    for comment in stream.comment_starts[seen:]:
        if comment >= start:
            return comment, 0
    consumed = stream.tell()
    if stream.hit_eof:
        return consumed, 0
    if marked[consumed - 1] in lexer.whitespace:
        return consumed - 1, 0
    return consumed - 1, 1


def _lex_tokens(marked: str) -> list[tuple[str, int, int]]:
    """Observe shlex stream events to bound tokens without its private state."""
    stream = _LexStream(marked)
    lexer = shlex.shlex(stream, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    tokens: list[tuple[str, int, int]] = []
    pushed_back = 0
    while True:
        begin = stream.tell() - pushed_back
        seen = len(stream.comment_starts)
        stream.hit_eof = False
        token = lexer.get_token()
        if token is None:
            break
        start = _skip_lexer_trivia(marked, begin, lexer)
        end, pushed_back = _token_end(marked, stream, seen, start, lexer)
        tokens.append((token, start, end))
    return tokens


@dataclass(frozen=True, slots=True)
class _LexedCommand:
    """Exact marked-text bounds; raw_spans[i] equals marked.text[slice(*bounds[i])].

    marked.source_span maps each bound pair to original-command coordinates.
    """

    tokens: list[str]
    bounds: list[tuple[int, int]]
    raw_spans: list[str]
    fully_single_quoted: list[bool]
    marked: SourceMappedText
    literals: list[StdinLiteral]
    redirects: dict[str, str]
    substitutions: dict[str, str]
    groups: dict[str, tuple[str, int]]


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
        token_spans = _lex_tokens(marked.text)
        tokens = [token for token, _, _ in token_spans]
        bounds = [(start, end) for _, start, end in token_spans]
        raw_spans = [marked.text[start:end] for start, end in bounds]
        fully_single_quoted = [raw == f"'{t}'" for t, raw in zip(tokens, raw_spans)]
    except (ValueError, TypeError):
        return None
    return _LexedCommand(
        tokens,
        bounds,
        raw_spans,
        fully_single_quoted,
        marked,
        literals,
        redirects,
        substitutions,
        groups,
    )


def _is_structural_token(lexed: _LexedCommand, index: int) -> bool:
    token = lexed.tokens[index]
    return lexed.raw_spans[index] == token and (
        token in lexed.groups
        or _HEREDOC_PLACEHOLDER_RE.fullmatch(token) is not None
        or token in lexed.redirects
        or token in _SHELL_OPERATORS
        or _is_case_terminator(token)
    )


def _herestring_at(lexed: _LexedCommand, index: int) -> tuple[StdinLiteral | None, int] | None:
    """Capture an unquoted herestring operator and its exact source word."""
    raw = lexed.raw_spans[index]
    if not raw.startswith("<<<"):
        return None
    fused = raw != "<<<"
    word = index if fused else index + 1
    if word >= len(lexed.tokens) or (not fused and _is_structural_token(lexed, word)):
        return None, index + 1
    offset = 3 if fused else 0
    start, end = lexed.bounds[word]
    raw_word = lexed.marked.text[start + offset : end]
    dequoted = lexed.tokens[word][offset:]
    text, _, _ = _restore_substitutions(dequoted, raw_word, False, lexed.substitutions)
    return (
        StdinLiteral(
            text,
            "herestring",
            raw_word != f"'{dequoted}'",
            lexed.marked.source_span(start + offset, end),
        ),
        word + 1,
    )


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
    tokens = lexed.tokens
    fully_single_quoted = lexed.fully_single_quoted
    raw_spans = lexed.raw_spans
    literals = lexed.literals
    redirects = lexed.redirects
    substitutions = lexed.substitutions
    groups = lexed.groups

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

        herestring = _herestring_at(lexed, index)
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
