"""The in-session rate-limit retry protocol remains explicitly covered."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.medium


def _protocol_section() -> str:
    skill = (
        Path(__file__).parents[2] / "src" / "autoskillit" / "skills" / "sous-chef" / "SKILL.md"
    ).read_text()
    return skill.split("## RATE LIMIT RETRY PROTOCOL", 1)[1].split("\n## ", 1)[0]


def test_sous_chef_rate_limit_retry_protocol_is_result_driven() -> None:
    section = _protocol_section()
    lowered = section.lower()

    assert "candidate_exhausted: true" in section
    assert "execution_selection.attempts" in section
    assert "retry_reason: rate_limited" in section
    assert "on_failure" in lowered and "on_rate_limit" in lowered, (
        "Rate-limit protocol must name the routing routes"
    )
    assert "execution_selection.continuation.resume_session_id" in section
    assert "same-backend/provider" in section
    assert "never rerun the" in lowered
    assert "sleep" not in lowered and "60 seconds" not in lowered, (
        "Rate-limit protocol must not prescribe a fixed wait before routing"
    )
