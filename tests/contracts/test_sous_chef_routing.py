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
        if in_routing_section and re.search(rf"retry_reason[:\s]+{re.escape(retry_reason)}", line):
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

    def test_summary_line_includes_stale(self) -> None:
        """The routing summary at the end of CONTEXT LIMIT ROUTING must mention stale."""
        skill_md = _sous_chef_text()
        assert "retry_reason=stale" in skill_md or "retry_reason: stale" in skill_md, (
            "SKILL.md must mention stale in the routing section"
        )
