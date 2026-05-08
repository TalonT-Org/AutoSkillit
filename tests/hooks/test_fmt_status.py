"""Tests for autoskillit.hooks.formatters._fmt_status."""

from __future__ import annotations


class TestFmtCloneRepo:
    """Test 6: _fmt_clone_repo renders new discriminator keys."""

    def test_fmt_clone_repo_renders_source_type_key(self) -> None:
        """New clone_source_type key is rendered in the flat-KV output."""
        from autoskillit.hooks.formatters._fmt_status import _fmt_clone_repo

        result = _fmt_clone_repo(
            {
                "clone_path": "/x",
                "source_dir": "/y",
                "remote_url": "u",
                "clone_source_type": "remote",
                "clone_source_reason": "ok",
            },
            False,
        )
        assert "clone_source_type: remote" in result

    def test_fmt_clone_repo_renders_local_source_type(self) -> None:
        """clone_source_type=local is rendered correctly."""
        from autoskillit.hooks.formatters._fmt_status import _fmt_clone_repo

        result = _fmt_clone_repo(
            {
                "clone_path": "/x",
                "source_dir": "/y",
                "remote_url": "",
                "clone_source_type": "local",
                "clone_source_reason": "strategy_clone_local",
            },
            False,
        )
        assert "clone_source_type: local" in result
        assert "clone_source_reason: strategy_clone_local" in result
