"""Behavioral guards for resolve-claims-review/SKILL.md.

Tests enforce fix-strategy taxonomy, protocol deviation and invalid statistics
classification rules, and needs_rerun structured output contract.
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-claims-review"
    / "SKILL.md"
)
SKILL_TEXT = SKILL_PATH.read_text() if SKILL_PATH.exists() else ""


def test_skill_path_exists() -> None:
    """SKILL.md must exist at the expected path."""
    assert SKILL_PATH.exists(), f"SKILL.md not found at {SKILL_PATH}"


def test_fix_strategies_defined() -> None:
    """All five fix strategies must be defined."""
    for strategy in [
        "add_citation",
        "qualify_claim",
        "remove_claim",
        "rerun_required",
        "design_flaw",
    ]:
        assert strategy in SKILL_TEXT, f"SKILL.md must define fix strategy '{strategy}'"


def test_protocol_deviation_rule_present() -> None:
    """SKILL.md must contain a protocol deviation classification rule mapping to rerun_required."""
    text = SKILL_TEXT.lower()
    assert "protocol deviation" in text, (
        "SKILL.md must define a protocol deviation classification rule"
    )
    pd_idx = text.find("protocol deviation")
    context = SKILL_TEXT[pd_idx : pd_idx + 600]
    assert "rerun_required" in context, (
        "Protocol deviation rule must map to rerun_required strategy"
    )


def test_invalid_statistics_rule_present() -> None:
    """SKILL.md must contain an invalid statistics rule mapping to rerun_required."""
    text = SKILL_TEXT.lower()
    assert "wrong unit of analysis" in text or "invalid statistic" in text, (
        "SKILL.md must define an invalid statistics classification rule"
    )
    for needle in ["wrong unit of analysis", "invalid statistic"]:
        idx = text.find(needle)
        if idx != -1:
            context = SKILL_TEXT[idx : idx + 600]
            assert "rerun_required" in context, (
                "Invalid statistics rule must map to rerun_required strategy"
            )
            break


def test_protocol_deviation_allows_justified_exception() -> None:
    """Protocol deviation rule must allow justified exceptions (not blanket rerun)."""
    text = SKILL_TEXT.lower()
    pd_idx = text.find("protocol deviation")
    assert pd_idx != -1
    context = text[pd_idx : pd_idx + 1000]
    assert "justified" in context or "justification" in context or "rationale" in context, (
        "Protocol deviation rule must allow justified exceptions"
    )


def test_rerun_required_covers_protocol_deviations() -> None:
    """Structured output description for rerun_required must cover protocol deviations."""
    text = SKILL_TEXT
    so_idx = text.find("## Structured Output")
    assert so_idx != -1, "SKILL.md must have Structured Output section"
    so_section = text[so_idx:]
    assert (
        "protocol deviation" in so_section.lower() or "execution diverged" in so_section.lower()
    ), "rerun_required structured output description must reference protocol deviations"


def test_needs_rerun_structured_output_documented() -> None:
    """SKILL.md must document needs_rerun emission format."""
    assert "needs_rerun" in SKILL_TEXT
    assert "needs_rerun = " in SKILL_TEXT
