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


def test_extract_positional_args_preserves_quoted_newline_value():
    from autoskillit.core import extract_positional_args

    result = extract_positional_args('/skill "/path/a.md\n/path/b.md" branch')
    assert result == ["/path/a.md\n/path/b.md", "branch"]


def test_extract_positional_args_raises_on_unmatched_quote():
    from autoskillit.core import extract_positional_args

    with pytest.raises(ValueError):
        extract_positional_args('/skill "/unclosed branch')


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


class TestEvaluateOutcomeExpression:
    """Cover the bool-typed input guard added to evaluate_outcome_expression.

    bool subclasses int in Python, so the function must reject bool-typed values
    explicitly — otherwise a True/False field would silently evaluate as 1/0 and
    corrupt outcome-invariant semantics.
    """

    def test_bool_field_returns_none(self):
        from autoskillit.core import evaluate_outcome_expression

        assert evaluate_outcome_expression("accept_count > 0", {"accept_count": True}) is None

    def test_false_bool_field_returns_none(self):
        from autoskillit.core import evaluate_outcome_expression

        assert evaluate_outcome_expression("accept_count > 0", {"accept_count": False}) is None

    def test_string_field_returns_none(self):
        from autoskillit.core import evaluate_outcome_expression

        assert evaluate_outcome_expression("accept_count > 0", {"accept_count": "5"}) is None

    def test_int_field_evaluates_truthily(self):
        from autoskillit.core import evaluate_outcome_expression

        assert evaluate_outcome_expression("accept_count > 0", {"accept_count": 5}) is True

    def test_missing_field_returns_none(self):
        from autoskillit.core import evaluate_outcome_expression

        assert evaluate_outcome_expression("accept_count > 0", {}) is None

    def test_conjunct_with_bool_returns_none(self):
        from autoskillit.core import evaluate_outcome_expression

        fields = {"accept_count": 5, "fix_failures": False}
        assert evaluate_outcome_expression(
            "accept_count > 0 and fix_failures == 0", fields
        ) is None
