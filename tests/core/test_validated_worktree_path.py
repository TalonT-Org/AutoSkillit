"""Tests for ValidatedWorktreePath construction contracts."""

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from autoskillit.core.claude_conventions import validate_worktree_path
from autoskillit.core.types import ValidatedWorktreePath

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestValidatedWorktreePathContracts:
    def test_rejects_nonexistent_directory(self):
        assert validate_worktree_path(Path("/nonexistent/xyz")) is None

    def test_rejects_relative_path(self, tmp_path):
        assert validate_worktree_path(Path("relative/path")) is None

    def test_accepts_real_directory(self, tmp_path):
        vwt = validate_worktree_path(tmp_path)
        assert vwt is not None
        assert isinstance(vwt, ValidatedWorktreePath)
        assert vwt.path == str(tmp_path)

    def test_is_frozen(self, tmp_path):
        vwt = validate_worktree_path(tmp_path)
        assert vwt is not None
        with pytest.raises(FrozenInstanceError):
            vwt.path = "/other"  # type: ignore[misc]

    def test_str_and_fspath(self, tmp_path):
        vwt = validate_worktree_path(tmp_path)
        assert vwt is not None
        assert str(vwt) == vwt.path
        assert os.fspath(vwt) == vwt.path
