"""Unit tests for extract_path_arg in core._type_helpers."""
from __future__ import annotations

import pytest

from autoskillit.core._type_helpers import _PATH_PREFIXES, extract_path_arg


class TestExtractPathArg:
    def test_clean_input_returns_path(self):
        result = extract_path_arg("/autoskillit:implement-worktree-no-merge /path/to/plan.md")
        assert result == "/path/to/plan.md"

    def test_trailing_markdown_header_ignored(self):
        cmd = "/autoskillit:implement-worktree-no-merge /path/plan.md\n\n## Base Branch\nimpl-926"
        assert extract_path_arg(cmd) == "/path/plan.md"

    def test_trailing_extra_token_ignored(self):
        assert (
            extract_path_arg("/autoskillit:implement-worktree-no-merge /path/plan.md impl-926")
            == "/path/plan.md"
        )

    def test_autoskillit_temp_prefix(self):
        cmd = "/autoskillit:implement-worktree-no-merge .autoskillit/temp/rectify/plan.md"
        assert extract_path_arg(cmd) == ".autoskillit/temp/rectify/plan.md"

    def test_dotslash_prefix(self):
        assert (
            extract_path_arg("/autoskillit:implement-worktree-no-merge ./path/plan.md")
            == "./path/plan.md"
        )

    def test_returns_none_no_path_token(self):
        assert extract_path_arg("/autoskillit:implement-worktree-no-merge impl-926") is None

    def test_strips_double_quotes(self):
        assert (
            extract_path_arg('/autoskillit:implement-worktree-no-merge "/path/plan.md"')
            == "/path/plan.md"
        )

    def test_strips_single_quotes(self):
        assert (
            extract_path_arg("/autoskillit:implement-worktree-no-merge '/path/plan.md'")
            == "/path/plan.md"
        )

    def test_returns_none_no_args(self):
        assert extract_path_arg("/autoskillit:implement-worktree-no-merge") is None


class TestPathPrefixesConstant:
    def test_is_tuple(self):
        assert isinstance(_PATH_PREFIXES, tuple)

    def test_contains_slash(self):
        assert "/" in _PATH_PREFIXES

    def test_contains_autoskillit_temp(self):
        assert ".autoskillit/" in _PATH_PREFIXES
