"""Original-command positions through the hook tokenizer's rewrites."""

import shlex

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from autoskillit.hooks._classification._source_map import SourceMapBuilder, SourceMappedText
from autoskillit.hooks._classification._tokenizer import (
    _lex_command,
    _lex_tokens,
    _tokenize_command_segments_with_redirects,
)
from autoskillit.hooks._runtime._command_classification import (
    all_evaluated_segments,
    evaluated_payloads,
    live_command_text,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def test_identity_source_map():
    source = SourceMappedText.identity("abc")
    assert source.text == "abc"
    assert source.starts == (0, 1, 2)
    assert source.ends == (1, 2, 3)


def test_replacement_source_map_composes_through_copies():
    builder = SourceMapBuilder(SourceMappedText.identity("a$(x)b"))
    builder.copy(0, 1)
    builder.emit("M", 1, 5)
    builder.copy(5, 6)
    first = builder.build()
    assert first.text == "aMb"
    assert first.source_span(1, 2) == (1, 5)
    assert first.source_span(0, 3) == (0, 6)
    second = SourceMapBuilder(first)
    second.copy(1, 2)
    assert second.build().source_span(0, 1) == (1, 5)


def test_dropped_text_remains_inside_combined_source_span():
    builder = SourceMapBuilder(SourceMappedText.identity("a\\\nb"))
    builder.copy(0, 1)
    builder.copy(1, 1)
    builder.copy(3, 4)
    source = builder.build()
    assert source.text == "ab"
    assert source.source_span(0, 2) == (0, 4)


@pytest.mark.parametrize("start,end", [(1, 1), (0, 99), (-1, 1)])
def test_source_span_rejects_empty_or_out_of_range_bounds(start, end):
    with pytest.raises(ValueError, match=r"empty or out-of-range span.*length 3"):
        SourceMappedText.identity("abc").source_span(start, end)


@pytest.mark.parametrize(
    ("marked", "expected"),
    [
        ("cat 'x' y", [("cat", "cat"), ("x", "'x'"), ("y", "y")]),
        ("cat  'x'  y", [("cat", "cat"), ("x", "'x'"), ("y", "y")]),
        (
            "echo x|'rm' z",
            [("echo", "echo"), ("x", "x"), ("|", "|"), ("rm", "'rm'"), ("z", "z")],
        ),
        ("true;echo hi", [("true", "true"), (";", ";"), ("echo", "echo"), ("hi", "hi")]),
        ("a\\  b", [("a ", "a\\ "), ("b", "b")]),
        ("a\\ ", [("a ", "a\\ ")]),
        ("a#c", [("a", "a")]),
        ("  # lead\n  tok", [("tok", "tok")]),
        ("e\\;;f", [("e;", "e\\;"), (";", ";"), ("f", "f")]),
        ("", []),
    ],
)
def test_lex_tokens_returns_exact_consumed_source_spans(marked, expected):
    spans = _lex_tokens(marked)
    assert [(token, marked[start:end]) for token, start, end in spans] == expected


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=list("ab ;|&'\\\"#\n\t$<"), max_size=24))
def test_lex_tokens_matches_shlex_and_each_span_relexes(marked):
    lexer = shlex.shlex(marked, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    try:
        expected_tokens = list(lexer)
    except ValueError:
        assume(False)

    spans = _lex_tokens(marked)
    assert [token for token, _, _ in spans] == expected_tokens
    previous_end = 0
    for token, start, end in spans:
        assert previous_end <= start < end <= len(marked)
        single = shlex.shlex(marked[start:end], posix=True, punctuation_chars=";&|")
        single.whitespace_split = True
        assert list(single) == [token]
        previous_end = end


@pytest.mark.parametrize(
    ("command", "token", "expected_source"),
    [
        ("echo a\\\nb", "ab", "a\\\nb"),
        ("echo x >out", "__AUTOSKILLIT_REDIRECT_0__", ">"),
        ("echo $(date) y", "__AUTOSKILLIT_SUBSTITUTION_0__", "$(date)"),
        ("(echo x)", "echo", "echo"),
        ("cat <<'EOF'\nbody\nEOF\nnext", "next", "next"),
        ("a\nb", ";", "\n"),
    ],
)
def test_lex_command_maps_named_token_bounds_to_original_source(command, token, expected_source):
    lexed = _lex_command(command)
    assert lexed is not None
    index = lexed.tokens.index(token)
    assert command[slice(*lexed.marked.source_span(*lexed.bounds[index]))] == expected_source


@settings(max_examples=300, deadline=None)
@given(
    st.lists(
        st.sampled_from(list("ab $();|&<>'\"\\\n#") + ["<<<", "$(c)", "2>", "<<EOF\nx\nEOF\n"]),
        max_size=12,
    ).map("".join)
)
def test_lex_command_source_spans_round_trip_without_overlap(command):
    lexed = _lex_command(command)
    assume(lexed is not None)
    assert lexed is not None

    source_spans = [lexed.marked.source_span(*bounds) for bounds in lexed.bounds]
    previous_end = 0
    for index, ((start, end), raw_span) in enumerate(zip(source_spans, lexed.raw_spans)):
        assert previous_end <= start < end <= len(command)
        assert raw_span == lexed.marked.text[slice(*lexed.bounds[index])]
        source = command[start:end]
        if "__AUTOSKILLIT_" not in raw_span and "\n" not in source:
            assert source == raw_span
        previous_end = end


def test_argv_tokens_preserve_exact_quote_and_operator_spans():
    quoted = _tokenize_command_segments_with_redirects("cat 'x' y")
    assert quoted is not None
    assert quoted[0].argv_tokens[1].raw_span == "'x'"
    assert quoted[0].argv_tokens[1].fully_single_quoted is True

    piped = _tokenize_command_segments_with_redirects("echo x|'rm' z")
    assert piped is not None
    assert piped[1].argv_tokens[0].raw_span == "'rm'"


@pytest.mark.parametrize(
    ("command", "raw_word", "text", "outer_expansion"),
    [
        ("cat <<< 'a b'", "'a b'", "a b", False),
        ("cat <<<'a b'", "'a b'", "a b", False),
        ("cat   <<<'x'", "'x'", "x", False),
        ("cat <<< 'x' | bash", "'x'", "x", False),
        ("cat <<< '|'", "'|'", "|", False),
        ('cat <<< ";"', '";"', ";", True),
        ("cat <<< \\&", "\\&", "&", True),
        ('cat <<< "a $(b)"', '"a $(b)"', "a $(b)", True),
        ("cat <<< word;echo", "word", "word", True),
        ("cat <<< $(echo hi)", "$(echo hi)", "$(echo hi)", True),
        ("cat <<< a\\\nb", "a\\\nb", "ab", True),
        ("echo 1\ncat <<< 'z'", "'z'", "z", False),
    ],
)
def test_herestring_literals_retain_exact_source_and_expansion(
    command, raw_word, text, outer_expansion
):
    segments = _tokenize_command_segments_with_redirects(command)
    assert segments is not None
    literal = next(literal for segment in segments for literal in segment.stdin_literals)
    assert literal.kind == "herestring"
    assert literal.source_span is not None
    assert command[slice(*literal.source_span)] == raw_word
    assert literal.text == text
    assert literal.outer_expansion is outer_expansion
    assert literal.feeds_stdin is True


@pytest.mark.parametrize(
    ("command", "tokens"),
    [
        (
            "git push '<<<' --force origin main",
            ["git", "push", "<<<", "--force", "origin", "main"],
        ),
        ("echo \\<<<x y", ["echo", "<<<x", "y"]),
    ],
)
def test_quoted_or_escaped_herestring_operator_is_an_ordinary_word(command, tokens):
    segments = _tokenize_command_segments_with_redirects(command)
    assert segments is not None
    assert [segment.tokens for segment in segments] == [tokens]
    assert all(segment.stdin_literals == () for segment in segments)


def test_herestring_does_not_consume_structural_token_as_its_word():
    command = "bash <<< 'git push --force origin main' <<< |"
    segments = _tokenize_command_segments_with_redirects(command)
    assert segments is not None
    assert len(segments[0].stdin_literals) == 1
    assert segments[0].stdin_literals[0].text == "git push --force origin main"
    assert segments[0].stdin_literals[0].feeds_stdin is True

    heredoc = "bash <<< <<'EOF'\ngit push --force origin main\nEOF\n"
    segments = _tokenize_command_segments_with_redirects(heredoc)
    assert segments is not None
    assert len(segments[0].stdin_literals) == 1
    assert segments[0].stdin_literals[0].kind == "heredoc"

    separated = _tokenize_command_segments_with_redirects("cat <<< ;echo x")
    assert separated is not None
    assert [segment.tokens for segment in separated] == [["cat"], ["echo", "x"]]
    assert all(segment.stdin_literals == () for segment in separated)


def test_evaluated_payloads_keep_herestring_substitution_source_spans():
    command = 'cat <<< "$(date)"'
    payloads = evaluated_payloads(command)
    assert [(payload.origin, payload.text) for payload in payloads] == [("substitution", "date")]
    payload = payloads[0]
    assert payload.source_span is not None
    assert command[slice(*payload.source_span)] == "date"


def test_only_double_quoted_herestring_substitution_is_outer_expansion():
    command = "cat <<< '$(a)'\"$(b)\""
    payloads = evaluated_payloads(command)
    assert [payload.text for payload in payloads if payload.origin == "substitution"] == ["b"]


def test_bash_herestring_payload_retains_raw_word_source_span():
    command = 'bash <<< "echo $(date)"'
    payloads = evaluated_payloads(command)
    assert len(payloads) == 1
    payload = payloads[0]
    assert (payload.origin, payload.text) == ("herestring", "echo $(date)")
    assert payload.source_span is not None
    assert command[slice(*payload.source_span)] == '"echo $(date)"'


def test_shell_fed_herestring_nested_substitution_is_walked_once():
    segments = all_evaluated_segments('bash <<< "echo $(date)"')
    assert segments is not None
    assert segments.count(["date"]) == 1


def test_unquoted_heredoc_still_exposes_one_outer_substitution():
    payloads = evaluated_payloads("cat <<EOF\n$(date)\nEOF")
    assert [payload.text for payload in payloads if payload.origin == "substitution"] == ["date"]


@pytest.mark.parametrize(
    ("command", "expected_push_count", "starts_with_cat_herestring"),
    [
        ("cat <<< 'git push --force origin main'", 0, True),
        ("bash <<< 'git push --force origin main'", 1, False),
        ('cat <<< "$(git push --force)"', 1, False),
        ('bash <<< "echo $(git push)"', 1, False),
        ("bash <<< 'git push --force origin main' <<< |", 1, False),
        ("bash <<< <<'EOF'\ngit push --force origin main\nEOF\n", 1, False),
    ],
)
def test_live_command_text_projects_herestrings_by_consumer(
    command, expected_push_count, starts_with_cat_herestring
):
    live = live_command_text(command)
    assert live.count("git push") == expected_push_count
    if starts_with_cat_herestring:
        assert live.startswith("cat <<<")
