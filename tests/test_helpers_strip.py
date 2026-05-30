"""Unit tests for strip_markdown_code_regions shared utility."""

from __future__ import annotations

import pytest

from tests._helpers import strip_markdown_code_regions

pytestmark = pytest.mark.small


def test_fenced_code_block_is_stripped() -> None:
    text = "prose before\n```python\n@dataclass\nclass Foo:\n    pass\n```\nprose after"
    result = strip_markdown_code_regions(text)
    assert "@dataclass" not in result
    assert "prose before" in result
    assert "prose after" in result


def test_inline_code_is_stripped() -> None:
    text = "Use `@mcp.tool()` for registration."
    result = strip_markdown_code_regions(text)
    assert "@mcp" not in result
    assert "Use" in result
    assert "for registration." in result


def test_prose_outside_code_is_preserved() -> None:
    text = "This is plain prose with no code blocks."
    result = strip_markdown_code_regions(text)
    assert result == text


def test_adjacent_fenced_blocks_both_stripped() -> None:
    text = "```\nblock one\n```\nmiddle prose\n```\nblock two\n```\nend"
    result = strip_markdown_code_regions(text)
    assert "block one" not in result
    assert "block two" not in result
    assert "middle prose" in result
    assert "end" in result


def test_multiple_inline_codes_stripped() -> None:
    text = "Call `foo()` and `bar()` then done."
    result = strip_markdown_code_regions(text)
    assert "foo()" not in result
    assert "bar()" not in result
    assert "Call" in result
    assert "then done." in result


def test_language_specifier_line_stripped_with_fence() -> None:
    text = "```python\nimport os\n```\nprose"
    result = strip_markdown_code_regions(text)
    assert "import os" not in result
    assert "python" not in result
    assert "prose" in result


def test_empty_string_returns_empty() -> None:
    assert strip_markdown_code_regions("") == ""


def test_no_code_regions_returns_unchanged() -> None:
    text = "Just some prose text.\nWith multiple lines."
    assert strip_markdown_code_regions(text) == text
