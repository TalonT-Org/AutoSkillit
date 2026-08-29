"""The in-session rate-limit retry protocol remains explicitly covered."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.medium


def test_sous_chef_rate_limit_retry_protocol_is_bounded() -> None:
    skill = (
        Path(__file__).parents[2] / "src" / "autoskillit" / "skills" / "sous-chef" / "SKILL.md"
    ).read_text()
    section = skill.split("RATE LIMIT RETRY PROTOCOL", 1)[1].split("##", 1)[0]
    assert "three" in section.lower() and "times consecutively" in section.lower()
    assert "session" in section.lower() and "deadline" in section.lower()
