"""Contract test: sous-chef SKILL.md must contain QUOTA WAIT PROTOCOL section."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def test_sous_chef_quota_wait_protocol_routes_structured_results():
    """Quota routing must use structured candidate exhaustion and continuation evidence."""
    skill_md = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "skills"
        / "sous-chef"
        / "SKILL.md"
    )
    content = skill_md.read_text()
    section = content.split("QUOTA WAIT PROTOCOL", 1)[1].split("##", 1)[0]
    assert "candidate_exhausted: true" in section
    assert "execution_selection.attempts" in section
    assert "execution_selection.continuation.resume_session_id" in section
    assert "on_rate_limit" in section and "on_failure" in section
    assert "sleep" not in section.lower()
