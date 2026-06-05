"""Tests for extract_positional_args helper."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_extract_positional_args_returns_all_tokens():
    from autoskillit.core import extract_positional_args

    result = extract_positional_args("/resolve-failures /worktrees/foo /plans/bar.md main")
    assert result == ["/worktrees/foo", "/plans/bar.md", "main"]


def test_extract_positional_args_returns_empty_for_no_args():
    from autoskillit.core import extract_positional_args

    result = extract_positional_args("/skill-name")
    assert result == []


def test_extract_positional_args_preserves_order():
    from autoskillit.core import extract_positional_args

    result = extract_positional_args("/skill /b/second /a/first baz")
    assert result == ["/b/second", "/a/first", "baz"]


def test_extract_positional_args_strips_quotes():
    from autoskillit.core import extract_positional_args

    result = extract_positional_args('/skill "/path/quoted" plain')
    assert result == ["/path/quoted", "plain"]


def test_extract_positional_args_returns_non_path_only():
    from autoskillit.core import extract_positional_args

    result = extract_positional_args("/skill-name foo bar")
    assert result == ["foo", "bar"]


class TestExtractSkillName:
    def test_slash_prefix(self):
        from autoskillit.core import extract_skill_name

        assert extract_skill_name("/test-skill args") == "test-skill"

    def test_namespaced_slash_prefix(self):
        from autoskillit.core import extract_skill_name

        assert extract_skill_name("/autoskillit:make-plan foo") == "make-plan"

    def test_dollar_prefix_returns_none(self):
        from autoskillit.core import extract_skill_name

        assert extract_skill_name("$test-skill args") is None

    def test_no_prefix_returns_none(self):
        from autoskillit.core import extract_skill_name

        assert extract_skill_name("test-skill args") is None
