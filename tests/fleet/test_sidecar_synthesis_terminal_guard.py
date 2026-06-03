"""Tests for sidecar synthesis terminal-step guard."""

from __future__ import annotations

import pytest

from autoskillit.fleet._sidecar_synthesis import synthesize_from_sidecar
from autoskillit.fleet.result_parser import L3ParseResult
from autoskillit.fleet.sidecar import IssueSidecarEntry

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

URL1 = "https://github.com/org/repo/issues/1"
URL2 = "https://github.com/org/repo/issues/2"
TS = "2026-06-01T00:00:00Z"
PR = "https://github.com/org/repo/pull/1"
PR2 = "https://github.com/org/repo/pull/2"

NO_SENTINEL = L3ParseResult(
    outcome="no_sentinel", payload=None, raw_body=None, parse_error=None, source="stdout"
)


def _entry(
    url: str = URL1,
    pr_url: str = PR,
    terminal_step: str | None = None,
) -> IssueSidecarEntry:
    return IssueSidecarEntry(
        issue_url=url, status="completed", ts=TS, pr_url=pr_url, terminal_step=terminal_step
    )


class TestSynthesisTerminalGuard:
    def test_blocked_when_terminal_step_is_failure(self) -> None:
        entries = [_entry(terminal_step="escalate_stop")]
        result = synthesize_from_sidecar(NO_SENTINEL, entries, 1)
        assert result is NO_SENTINEL

    def test_blocked_when_terminal_step_is_release_issue_failure(self) -> None:
        entries = [_entry(terminal_step="release_issue_failure")]
        result = synthesize_from_sidecar(NO_SENTINEL, entries, 1)
        assert result is NO_SENTINEL

    def test_allowed_when_terminal_step_is_done(self) -> None:
        entries = [_entry(terminal_step="done")]
        result = synthesize_from_sidecar(NO_SENTINEL, entries, 1)
        assert result is not NO_SENTINEL
        assert result.outcome == "completed_clean"
        assert result.payload is not None
        assert result.payload["success"] is True

    def test_allowed_when_no_terminal_step(self) -> None:
        entries = [_entry(terminal_step=None)]
        result = synthesize_from_sidecar(NO_SENTINEL, entries, 1)
        assert result is not NO_SENTINEL
        assert result.outcome == "completed_clean"
        assert result.payload is not None
        assert result.payload["success"] is True

    def test_blocked_when_any_entry_has_failure_terminal(self) -> None:
        entries = [
            _entry(url=URL1, terminal_step="done"),
            _entry(url=URL2, pr_url=PR2, terminal_step="escalate_stop"),
        ]
        result = synthesize_from_sidecar(NO_SENTINEL, entries, 2)
        assert result is NO_SENTINEL
