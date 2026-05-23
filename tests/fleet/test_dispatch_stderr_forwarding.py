"""Stderr envelope forwarding tests for fleet dispatch."""

from __future__ import annotations

import dataclasses

import pytest

from tests.fakes import _DEFAULT_SKILL_RESULT, InMemoryHeadlessExecutor
from tests.fleet._helpers import (
    _make_completed_clean,
    _make_completed_dirty,
    _make_no_sentinel,
    _run,
    _setup_dispatch,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestStderrEnvelopeForwarding:
    """Verify that stderr from SkillResult is forwarded in all dispatch envelope shapes."""

    @pytest.mark.anyio
    async def test_completed_clean_envelope_includes_stderr(self, tool_ctx, monkeypatch):
        """completed_clean envelope includes stderr field with skill_result.stderr value."""
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, stderr="some error output")
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        result = await _run(tool_ctx)
        assert result["stderr"] == "some error output"

    @pytest.mark.anyio
    async def test_completed_dirty_envelope_includes_stderr(self, tool_ctx, monkeypatch):
        """completed_dirty envelope includes stderr field with skill_result.stderr value."""
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, stderr="parse failure trace")
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_dirty(),
        )

        result = await _run(tool_ctx)
        assert result["stderr"] == "parse failure trace"

    @pytest.mark.anyio
    async def test_no_sentinel_envelope_includes_stderr(self, tool_ctx, monkeypatch):
        """no_sentinel envelope includes stderr field with skill_result.stderr value."""
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT, stderr="missing sentinel trace"
            )
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert result["stderr"] == "missing sentinel trace"

    @pytest.mark.anyio
    async def test_no_sentinel_envelope_stderr_truncated_to_envelope_max(
        self, tool_ctx, monkeypatch
    ):
        """no_sentinel envelope stderr is truncated when stderr exceeds ENVELOPE_STDERR_MAX."""
        _setup_dispatch(tool_ctx, monkeypatch)
        long_stderr = "x" * 3000
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(_DEFAULT_SKILL_RESULT, stderr=long_stderr)
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_no_sentinel(),
        )

        result = await _run(tool_ctx)
        assert len(result["stderr"]) < 3000
        assert "truncated" in result["stderr"]

    @pytest.mark.anyio
    async def test_timeout_envelope_includes_stderr_in_details(self, tool_ctx, monkeypatch):
        """Timeout fleet_error envelope includes stderr in details dict."""
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT, subtype="timeout", stderr="timeout stderr"
            )
        )

        result = await _run(tool_ctx)
        assert result["stderr"] == "timeout stderr"

    @pytest.mark.anyio
    async def test_timeout_envelope_stderr_truncated_to_envelope_max(self, tool_ctx, monkeypatch):
        """Timeout envelope stderr is truncated when stderr exceeds ENVELOPE_STDERR_MAX."""
        _setup_dispatch(tool_ctx, monkeypatch)
        long_stderr = "x" * 3000
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT, subtype="timeout", stderr=long_stderr
            )
        )

        result = await _run(tool_ctx)
        assert len(result["stderr"]) < 3000
        assert "truncated" in result["stderr"]

    @pytest.mark.anyio
    async def test_envelope_stderr_truncated_to_envelope_max(self, tool_ctx, monkeypatch):
        """When skill_result.stderr exceeds ENVELOPE_STDERR_MAX, envelope truncates it."""
        _setup_dispatch(tool_ctx, monkeypatch)
        long_stderr = "x" * 3000
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT, stderr=long_stderr, success=False
            )
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=False),
        )

        result = await _run(tool_ctx)
        assert len(result["stderr"]) < 3000
        assert "truncated" in result["stderr"]

    @pytest.mark.anyio
    async def test_completed_dirty_envelope_stderr_truncated_to_envelope_max(
        self, tool_ctx, monkeypatch
    ):
        """completed_dirty envelope stderr is truncated when stderr exceeds ENVELOPE_STDERR_MAX."""
        _setup_dispatch(tool_ctx, monkeypatch)
        long_stderr = "x" * 3000
        tool_ctx.executor = InMemoryHeadlessExecutor(
            default_result=dataclasses.replace(
                _DEFAULT_SKILL_RESULT, stderr=long_stderr, success=False
            )
        )
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_dirty(),
        )

        result = await _run(tool_ctx)
        assert len(result["stderr"]) < 3000
        assert "truncated" in result["stderr"]

    @pytest.mark.anyio
    async def test_envelope_stderr_empty_when_no_stderr(self, tool_ctx, monkeypatch):
        """When skill_result.stderr is empty, envelope stderr field is empty string."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.parse_l3_result_block",
            lambda **_: _make_completed_clean(success=True),
        )

        result = await _run(tool_ctx)
        assert result["stderr"] == ""
