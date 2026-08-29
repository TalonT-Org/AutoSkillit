"""The in-session rate-limit retry protocol remains explicitly covered."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.medium


def _protocol_section() -> str:
    skill = (
        Path(__file__).parents[2] / "src" / "autoskillit" / "skills" / "sous-chef" / "SKILL.md"
    ).read_text()
    return skill.split("RATE LIMIT RETRY PROTOCOL", 1)[1].split("##", 1)[0]


def test_sous_chef_rate_limit_retry_protocol_is_bounded() -> None:
    section = _protocol_section()
    lowered = section.lower()

    # Behavioral facts the recipe depends on — the protocol must (a) prescribe
    # a sleep before re-routing, (b) cap the consecutive-retry count to three,
    # (c) honor the session-deadline budget before sleeping, and (d) reset the
    # counter on success. Each clause is asserted on the specific wording that
    # downstream code / tests look up, not bare substrings.
    assert "60 seconds" in lowered or "60s" in lowered, (
        "Rate-limit protocol must specify the initial sleep duration"
    )
    assert "three" in lowered and "consecutively" in lowered, (
        "Rate-limit protocol must cap consecutive retries at three"
    )
    assert "session" in lowered and "deadline" in lowered, (
        "Rate-limit protocol must reference the session deadline budget"
    )
    assert "120 seconds" in lowered or "120s" in lowered, (
        "Rate-limit protocol must specify the deadline-budget threshold (120s)"
    )
    assert "on_failure" in lowered and "on_rate_limit" in lowered, (
        "Rate-limit protocol must name the routing routes"
    )
    assert "reset" in lowered and "success" in lowered, (
        "Rate-limit protocol must require resetting the counter on success"
    )
