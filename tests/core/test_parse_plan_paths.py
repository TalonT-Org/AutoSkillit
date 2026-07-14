"""parse_plan_paths unit tests — covers comma/newline splitter used by file_path_list gate."""

from __future__ import annotations

import pytest

from autoskillit.core import parse_plan_paths

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_comma_separated():
    assert parse_plan_paths("/a.md,/b.md") == ("/a.md", "/b.md")


def test_newline_separated():
    assert parse_plan_paths("/a.md\n/b.md") == ("/a.md", "/b.md")


def test_mixed_delimiters():
    assert parse_plan_paths("/a.md,/b.md\n/c.md") == ("/a.md", "/b.md", "/c.md")


def test_whitespace_stripping():
    assert parse_plan_paths(" /a.md , /b.md ") == ("/a.md", "/b.md")


def test_empty_tokens_filtered():
    assert parse_plan_paths("/a.md,,/b.md") == ("/a.md", "/b.md")


def test_single_path():
    assert parse_plan_paths("/a.md") == ("/a.md",)
