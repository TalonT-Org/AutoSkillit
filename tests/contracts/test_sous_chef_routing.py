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


def _extract_routing_section(skill_md: str) -> str:
    """Extract the full CONTEXT LIMIT ROUTING section."""
    lines = skill_md.splitlines()
    in_section = False
    extracted: list[str] = []
    for line in lines:
        if "CONTEXT LIMIT ROUTING" in line:
            in_section = True
            extracted.append(line)
            continue
        if in_section and line.startswith("---"):
            break
        if in_section:
            extracted.append(line)
    return "\n".join(extracted)


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
    idx = text.index("review_pr")
    assert "NEVER" in text[max(0, idx - 400) : idx + 200]


def test_sous_chef_contains_context_ownership_line() -> None:
    """The STEP EXECUTION section must contain a context-ownership assertion.

    The model must be told that context management is handled by the system
    so it cannot rationalize step-skipping with context-pressure arguments.
    The assertion must appear within 600 characters of the STEP EXECUTION sentinel.
    """
    content = _sous_chef_text()
    idx = content.find("STEP EXECUTION IS NOT DISCRETIONARY")
    assert idx >= 0, "STEP EXECUTION sentinel missing from sous-chef SKILL.md"
    section = content[idx : idx + 600]
    assert "on_context_limit routing" in section, (
        "Context-ownership line missing from STEP EXECUTION section. "
        "The model must be told the system handles context via on_context_limit routing."
    )


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


def _extract_multiple_issues_section(skill_md: str) -> str:
    """Extract text from '## MULTIPLE ISSUES' up to the next top-level '## ' heading."""
    lines = skill_md.splitlines()
    in_section = False
    extracted: list[str] = []
    for line in lines:
        if line.startswith("## MULTIPLE ISSUES"):
            in_section = True
            extracted.append(line)
            continue
        if in_section and line.startswith("## ") and "MULTIPLE ISSUES" not in line:
            break
        if in_section:
            extracted.append(line)
    return "\n".join(extracted)


def test_sous_chef_invokes_execution_map_before_parallel_dispatch() -> None:
    """MULTIPLE ISSUES section must invoke /autoskillit:build-execution-map before dispatch."""
    skill_md = _sous_chef_text()
    multiple_issues = _extract_multiple_issues_section(skill_md)
    assert multiple_issues, "MULTIPLE ISSUES section not found in sous-chef/SKILL.md"
    assert "/autoskillit:build-execution-map" in multiple_issues, (
        "MULTIPLE ISSUES section must invoke /autoskillit:build-execution-map before "
        "launching parallel pipelines"
    )
    # The invocation must come before launching pipelines
    map_idx = multiple_issues.find("/autoskillit:build-execution-map")
    pipeline_idx = multiple_issues.find("pipeline")
    assert map_idx < pipeline_idx, (
        "build-execution-map invocation must appear before parallel pipeline launch "
        "in MULTIPLE ISSUES section"
    )


def test_sous_chef_group_merge_wait_before_next_group() -> None:
    """Sous-chef must require merge-wait between execution map groups."""
    skill_md = _sous_chef_text()
    assert "Group N+1" in skill_md or "group N+1" in skill_md.lower(), (
        "sous-chef SKILL.md must contain a Group N+1 merge-wait rule"
    )
    # Check the actual merge-wait language is present
    lower = skill_md.lower()
    assert "merge" in lower and ("wait" in lower or "before" in lower), (
        "sous-chef SKILL.md must instruct that Group N+1 waits for Group N's PRs to merge"
    )


def test_merge_phase_skips_merge_prs_for_queue_mode() -> None:
    """MERGE PHASE must instruct the orchestrator NOT to invoke merge-prs
    when queue_available=true (and sequential_queue is not set to true)."""
    merge_phase = _extract_merge_phase_section(_sous_chef_text())

    assert "queue_available" in merge_phase, (
        "MERGE PHASE must mention queue_available to distinguish queue vs classic routing"
    )
    lower = merge_phase.lower()
    assert any(
        phrase in lower
        for phrase in [
            "do not invoke merge-prs",
            "do not invoke `merge-prs`",
            "skip merge-prs",
            "skip `merge-prs`",
            "not invoke merge-prs",
            "not invoke `merge-prs`",
        ]
    ), "MERGE PHASE must explicitly say NOT to invoke merge-prs when queue_available=true"


def test_merge_phase_documents_sequential_queue_override() -> None:
    """MERGE PHASE must document the sequential_queue hidden ingredient as
    a force-override that routes through merge-prs even for queue-mode repos."""
    merge_phase = _extract_merge_phase_section(_sous_chef_text())

    assert "sequential_queue" in merge_phase, (
        "MERGE PHASE must document the sequential_queue hidden ingredient override"
    )


def test_merge_phase_preserves_merge_prs_for_non_queue_repos() -> None:
    """MERGE PHASE must still route to merge-prs when queue_available=false
    (the classic batch-branch path is unchanged)."""
    merge_phase = _extract_merge_phase_section(_sous_chef_text())

    lower = merge_phase.lower()
    assert "merge-prs" in lower, (
        "MERGE PHASE must still mention merge-prs for the queue_available=false classic path"
    )
    assert "queue_available" in lower and "false" in lower, (
        "MERGE PHASE must distinguish queue_available=false as the condition for merge-prs"
    )


def test_sous_chef_no_resume_session_id_in_context_limit_routing() -> None:
    """sous-chef SKILL.md must not instruct passing resume_session_id for
    on_context_limit routing.

    Context-exhausted sessions must start fresh to get a full context window.
    """
    skill_md = _sous_chef_text()
    assert "resume_session_id" not in skill_md, (
        "sous-chef SKILL.md must not instruct passing resume_session_id; "
        "context-exhausted sessions must start fresh"
    )


class TestWorktreeStaleCarveout:
    """SKILL.md routing contract for the worktree-stale carve-out."""

    def test_worktree_stale_carveout_exists(self) -> None:
        """SKILL.md must contain a worktree-stale carve-out for retry_reason=stale."""
        skill_md = _sous_chef_text()
        routing_section = _extract_routing_section(skill_md)
        assert "worktree" in routing_section.lower() and "stale" in routing_section.lower(), (
            "CONTEXT LIMIT ROUTING must contain a worktree-stale carve-out"
        )
        assert "implement-worktree-no-merge" in routing_section, (
            "Worktree-stale carve-out must explicitly name implement-worktree-no-merge"
        )

    def test_worktree_stale_carveout_does_not_route_to_on_context_limit(self) -> None:
        """Worktree-stale carve-out must NOT route to on_context_limit."""
        skill_md = _sous_chef_text()
        routing_section = _extract_routing_section(skill_md)
        lines = routing_section.splitlines()
        carveout_lines: list[str] = []
        in_carveout = False
        for line in lines:
            if "worktree" in line.lower() and "stale" in line.lower() and "carve" in line.lower():
                in_carveout = True
                carveout_lines.append(line)
                continue
            if in_carveout:
                if line.startswith("**") or line.startswith("---"):
                    break
                carveout_lines.append(line)
        carveout_text = "\n".join(carveout_lines)
        assert carveout_text, "Expected to find a worktree-stale carve-out subsection"
        assert "on_context_limit" not in carveout_text, (
            "Worktree-stale carve-out must not route to on_context_limit — "
            "stale is not a context limit"
        )

    def test_worktree_stale_carveout_bypasses_retries_budget(self) -> None:
        """Worktree-stale carve-out must bypass the retries budget."""
        skill_md = _sous_chef_text()
        lower = skill_md.lower()
        assert any(
            phrase in lower
            for phrase in [
                "without consuming the retries budget",
                "without decrementing the retries counter",
                "does not consume the retries budget",
                "does not decrement the retries",
                "bypass the retries",
                "independent of the retries counter",
                "regardless of the retries counter",
            ]
        ), "Worktree-stale carve-out must explicitly state it bypasses the retries budget"

    def test_worktree_stale_carveout_is_one_shot(self) -> None:
        """Worktree-stale carve-out must be limited to a single retry."""
        skill_md = _sous_chef_text()
        lower = skill_md.lower()
        assert any(
            phrase in lower
            for phrase in [
                "one-shot",
                "once",
                "single retry",
                "maximum once",
                "at most once",
                "if the retry also goes stale",
            ]
        ), "Worktree-stale carve-out must be one-shot (limited to a single stale retry)"


def test_prompts_worktree_stale_carveout() -> None:
    """_prompts.py orchestrator prompt must contain the worktree-stale carve-out."""
    prompts_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "cli"
        / "_prompts.py"
    )
    prompts_text = prompts_path.read_text().lower()
    assert "worktree" in prompts_text and "stale" in prompts_text, (
        "_prompts.py must contain a worktree-stale carve-out matching SKILL.md"
    )
