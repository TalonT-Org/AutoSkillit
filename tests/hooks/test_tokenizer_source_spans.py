"""Original-command positions through the hook tokenizer's rewrites."""

import pytest

from autoskillit.hooks._classification._source_map import SourceMapBuilder, SourceMappedText

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
    with pytest.raises(ValueError, match="empty or out-of-range span"):
        SourceMappedText.identity("abc").source_span(start, end)
