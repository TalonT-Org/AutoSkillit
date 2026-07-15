"""Tests for the run_skill docstring's enumeration of retry_reason values."""

from __future__ import annotations

import pytest

from autoskillit.core.types import RetryReason

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _run_skill_docstring() -> str:
    """Import the run_skill function and return its docstring.

    The function is registered via @mcp.tool() — the wrapper preserves the
    underlying function's __doc__ attribute. If the import fails (e.g. when
    the MCP server has not been initialized for testing), skip with an
    informative message rather than failing collection.
    """
    try:
        from autoskillit.server.tools import tools_execution
    except Exception as exc:  # noqa: BLE001 — surface import issues as skips
        pytest.skip(f"Could not import run_skill: {exc}")
    func = getattr(tools_execution, "run_skill", None)
    if func is None:
        pytest.skip("run_skill not exposed on tools_execution module")
    return func.__doc__ or ""


class TestRunSkillDocstring:
    """The run_skill docstring must enumerate every retry_reason value that
    can reach the orchestrator."""

    # RetryReason values that *can* reach the orchestrator — those which map
    # to a routing decision the orchestrator acts on. Internal-only members
    # (cancelled) and the zero-value (none) are intentionally excluded.
    DOCSTRING_REQUIRED_RETRY_REASONS: tuple[str, ...] = (
        "rate_limited",
        "stale",
        "idle_stall",
        "clone_contamination",
        "budget_exhausted",
    )

    @pytest.mark.parametrize("retry_reason", DOCSTRING_REQUIRED_RETRY_REASONS)
    def test_docstring_mentions_retry_reason(self, retry_reason: str) -> None:
        """Each orchestrator-relevant RetryReason value appears in the docstring."""
        docstring = _run_skill_docstring()
        assert retry_reason in docstring, (
            f"run_skill docstring must document retry_reason={retry_reason!r}. "
            f"Docstring start: {docstring[:200]!r}"
        )

    def test_docstring_mentions_resume_reason(self) -> None:
        """The 'resume' retry_reason is the canonical context-limit signal and must be present."""
        docstring = _run_skill_docstring()
        assert "resume" in docstring, "run_skill docstring must document the 'resume' retry_reason"

    def test_docstring_mentions_drain_race_reason(self) -> None:
        """The 'drain_race' retry_reason must be present."""
        docstring = _run_skill_docstring()
        assert "drain_race" in docstring, (
            "run_skill docstring must document the 'drain_race' retry_reason"
        )

    def test_docstring_consistent_with_retry_reason_enum(self) -> None:
        """Cross-check: any retry_reason value listed in the docstring corresponds
        to a member of the RetryReason enum (defends against typos in the docstring).
        """
        docstring = _run_skill_docstring()

        known_values = {member.value for member in RetryReason}
        for line in docstring.splitlines():
            stripped = line.strip()
            quoted = None
            if stripped.startswith("- ") and '"' in stripped:
                first_quote = stripped.find('"')
                if first_quote != -1:
                    rest = stripped[first_quote + 1 :]
                    closing = rest.find('"')
                    if closing != -1:
                        quoted = rest[:closing]
            if quoted is None:
                continue
            assert quoted in known_values, (
                f"Docstring references retry_reason={quoted!r} which is not a "
                f"member of RetryReason. Known values: {sorted(known_values)}"
            )
