"""Tests for autoskillit.hooks.formatters._fmt_status."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


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


class TestFmtGetTokenSummaryCanonicalKeys:
    """Verify _fmt_get_token_summary works with canonical-only field names."""

    def test_fmt_get_token_summary_canonical_keys_only(self) -> None:
        from autoskillit.hooks.formatters._fmt_status import _fmt_get_token_summary

        data = {
            "steps": [
                {
                    "step_name": "implement",
                    "input_tokens": 5000,
                    "output_tokens": 1200,
                    "cache_write_tokens": 200,
                    "cache_read_tokens": 3000,
                    "invocation_count": 1,
                    "wall_clock_seconds": 60.0,
                }
            ],
            "total": {
                "input_tokens": 5000,
                "output_tokens": 1200,
                "cache_write_tokens": 200,
                "cache_read_tokens": 3000,
            },
            "mcp_responses": {"steps": [], "total": {}},
        }
        result = _fmt_get_token_summary(data, False)
        assert "cr:3.0k" in result
        assert "cw:200" in result
        assert "total_cache_read: 3.0k" in result
        assert "total_cache_write: 200" in result

    def test_fmt_get_token_summary_legacy_keys_not_normalized(self) -> None:
        from autoskillit.hooks.formatters._fmt_status import _fmt_get_token_summary

        data = {
            "steps": [
                {
                    "step_name": "plan",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 999,
                    "cache_read_input_tokens": 888,
                    "invocation_count": 1,
                    "wall_clock_seconds": 10.0,
                }
            ],
            "total": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 999,
                "cache_read_input_tokens": 888,
            },
            "mcp_responses": {"steps": [], "total": {}},
        }
        result = _fmt_get_token_summary(data, False)
        assert "cr:0" in result
        assert "cw:0" in result
        assert "total_cache_read: 0" in result
        assert "total_cache_write: 0" in result
