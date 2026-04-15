"""Contract tests for the CONTEXT LIMIT ROUTING section in sous-chef SKILL.md."""

from __future__ import annotations

import re
from pathlib import Path


def _sous_chef_text() -> str:
    skill_md = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "skills"
        / "sous-chef"
        / "SKILL.md"
    )
    return skill_md.read_text()


def _extract_routing_rule(skill_md: str, retry_reason: str) -> str:
    """Extract the bullet(s) in CONTEXT LIMIT ROUTING that mention a given retry_reason."""
    lines = skill_md.splitlines()
    in_routing_section = False
    extracted: list[str] = []
    for line in lines:
        if "CONTEXT LIMIT ROUTING" in line:
            in_routing_section = True
            continue
        if in_routing_section and line.startswith("---"):
            break
        if in_routing_section and re.search(
            rf"retry_reason[:\s=]+{re.escape(retry_reason)}", line
        ):
            extracted.append(line)
    return "\n".join(extracted)


class TestSousChefStaleRouting:
    """SKILL.md routing contract for retry_reason=stale."""

    def test_stale_routing_rule_exists(self) -> None:
        """SKILL.md must contain a routing rule for retry_reason: stale."""
        skill_md = _sous_chef_text()
        assert "retry_reason: stale" in skill_md, (
            "SKILL.md CONTEXT LIMIT ROUTING section must include a rule for retry_reason: stale"
        )

    def test_stale_routing_does_not_route_to_on_context_limit(self) -> None:
        """retry_reason=stale must NOT route to on_context_limit."""
        skill_md = _sous_chef_text()
        stale_section = _extract_routing_rule(skill_md, "stale")
        assert stale_section, "Expected to find a stale routing rule in SKILL.md"
        assert "on_context_limit" not in stale_section, (
            "retry_reason=stale must not route to on_context_limit — "
            "stale is a transient failure, not a context limit"
        )

    def test_stale_routing_routes_to_retries_or_on_failure(self) -> None:
        """retry_reason=stale must route via retries counter or on_failure."""
        skill_md = _sous_chef_text()
        stale_section = _extract_routing_rule(skill_md, "stale")
        assert stale_section, "Expected to find a stale routing rule in SKILL.md"
        assert "retries" in stale_section or "on_failure" in stale_section, (
            "retry_reason=stale must route via retries counter or on_failure"
        )

    def test_stale_routing_uses_subtype_discriminant(self) -> None:
        """sous-chef/SKILL.md must contain 'subtype: stale' as a compound routing discriminant."""
        skill_md = _sous_chef_text()
        assert "subtype: stale" in skill_md or "subtype=stale" in skill_md, (
            "sous-chef/SKILL.md must contain 'subtype: stale' or 'subtype=stale' as a "
            "compound routing discriminant, not just the words 'stale' and 'subtype' separately"
        )


def _extract_merge_phase_section(skill_md: str) -> str:
    """Extract text from '## MERGE PHASE' up to the next top-level '## ' heading."""
    lines = skill_md.splitlines()
    in_section = False
    extracted: list[str] = []
    for line in lines:
        if line.startswith("## MERGE PHASE"):
            in_section = True
            extracted.append(line)
            continue
        if in_section and line.startswith("## ") and "MERGE PHASE" not in line:
            break
        if in_section:
            extracted.append(line)
    return "\n".join(extracted)


def test_sous_chef_contains_step_execution_obligation() -> None:
    text = _sous_chef_text()
    assert "STEP EXECUTION IS NOT DISCRETIONARY" in text
    assert "MUST execute every step" in text
    assert "NEVER skip a step because" in text


def test_sous_chef_contains_pr_pipeline_protection() -> None:
    text = _sous_chef_text()
    assert "review_pr" in text
    assert "annotate_pr_diff" in text
    assert "compose_pr" in text
    # Must follow the pattern of the merge protection: named NEVER rule
    assert "NEVER" in text[text.index("review_pr") - 200 : text.index("review_pr") + 200]


def test_sous_chef_merge_phase_documents_queue_no_auto_path() -> None:
    """MERGE PHASE section must document the queue_enqueue_no_auto routing cell."""
    skill_md = _sous_chef_text()
    merge_phase = _extract_merge_phase_section(skill_md)
    assert merge_phase, "MERGE PHASE section not found in sous-chef/SKILL.md"
    assert "queue_enqueue_no_auto" in merge_phase, (
        "MERGE PHASE section must document the queue_enqueue_no_auto step"
    )
    assert "queue_available == true and auto_merge_available == false" in merge_phase, (
        "MERGE PHASE section must document the condition "
        "'queue_available == true and auto_merge_available == false'"
    )
