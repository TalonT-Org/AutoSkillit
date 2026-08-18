"""SKILL.md GraphQL invocation completeness contract.

Every section that instructs the agent to execute a GraphQL operation must
contain a fenced ``bash`` block with a concrete ``gh api graphql`` invocation
in that same section. This covers:

1. Fenced ```graphql blocks (regardless of parameterization style)
2. Prose-described GraphQL operations (no fenced block)

Both detectors operate at ##-level section granularity.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.core.paths import pkg_root
from autoskillit.hooks._github_mutation_analysis import (
    GitHubMutationStatus,
    analyze_github_mutations,
)
from autoskillit.hooks.guards.github_mutation_guard import ParsedHookCommand, decide
from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_graphql_blocks,
    extract_sections,
    has_prose_graphql_execution,
)
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_SKILLS_DIRS = [pkg_root() / "skills", pkg_root() / "skills_extended"]

_GH_API_GRAPHQL_RE = re.compile(r"gh\s+api\s+graphql\b")
_GRAPHQL_INPUT_RE = re.compile(r'--input(?:\s+|=)["\']?([^"\'\s]+)')

_INPUT_PAYLOAD_STATUSES: dict[str, GitHubMutationStatus] = {
    "apply_labels_chunk_0.json": GitHubMutationStatus.SINGLE_RESOLVED,
    "auto_merge_query.json": GitHubMutationStatus.NONE,
    "blocker_labels.json": GitHubMutationStatus.SINGLE_RESOLVED,
    "create_issues_chunk_0.json": GitHubMutationStatus.SINGLE_RESOLVED,
    "create_missing_labels.json": GitHubMutationStatus.SINGLE_RESOLVED,
    "label_definitions.json": GitHubMutationStatus.SINGLE_RESOLVED,
    "merge_queue_query.json": GitHubMutationStatus.NONE,
    "pr_batch_0.json": GitHubMutationStatus.NONE,
    "pr_bodies_batch_0.json": GitHubMutationStatus.NONE,
    "pr_status_batch_0.json": GitHubMutationStatus.NONE,
    "resolve_threads_chunk_0.json": GitHubMutationStatus.SINGLE_RESOLVED,
    "thread_query.json": GitHubMutationStatus.NONE,
    "watermark_query.json": GitHubMutationStatus.NONE,
}
_INLINE_GRAPHQL_STATUSES: dict[str, GitHubMutationStatus] = {
    "resolve-review": GitHubMutationStatus.SINGLE_RESOLVED,
    "review-pr": GitHubMutationStatus.NONE,
}


def _all_skill_dirs() -> list[Path]:
    dirs = []
    for base in _SKILLS_DIRS:
        if base.exists():
            dirs.extend(d for d in base.iterdir() if d.is_dir())
    return dirs


def _materialize_graphql_input(
    block: str,
    *,
    tmp_path: Path,
) -> tuple[str, GitHubMutationStatus]:
    substitutions: dict[str, str] = {
        "{{AUTOSKILLIT_TEMP}}": str(tmp_path),
        "/absolute/audit-run": str(tmp_path / "audit-run"),
        "/absolute/project-temp": str(tmp_path),
    }
    command = block
    for placeholder, replacement in substitutions.items():
        command = command.replace(placeholder, replacement)

    match = _GRAPHQL_INPUT_RE.search(command)
    assert match is not None
    payload_path = Path(match.group(1))
    expected_status = _INPUT_PAYLOAD_STATUSES[payload_path.name]
    query = (
        'mutation { deleteIssue(input: {issueId: "I_1"}) { clientMutationId } }'
        if expected_status is GitHubMutationStatus.SINGLE_RESOLVED
        else "query { viewer { login } }"
    )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps({"query": query, "variables": {}}), encoding="utf-8")
    return command, expected_status


def test_bundled_graphql_invocations_pass_runtime_guard(tmp_path: Path) -> None:
    failures: list[str] = []

    for skill_dir in _all_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        for block in extract_bash_blocks(skill_md.read_text(encoding="utf-8")):
            if not _GH_API_GRAPHQL_RE.search(block):
                continue
            match = _GRAPHQL_INPUT_RE.search(block)
            if match is not None:
                command, expected_status = _materialize_graphql_input(block, tmp_path=tmp_path)
            else:
                command = block
                expected_status = _INLINE_GRAPHQL_STATUSES[skill_dir.name]

            analysis = analyze_github_mutations(command, cwd=str(tmp_path))
            decision = decide(ParsedHookCommand("bash", command, str(tmp_path), str(tmp_path), ()))
            expected_count = 1 if expected_status is GitHubMutationStatus.SINGLE_RESOLVED else 0
            if (
                analysis.status is not expected_status
                or analysis.request_count != expected_count
                or not decision.allow
            ):
                failures.append(
                    f"{skill_dir.name}: status={analysis.status.value}, "
                    f"count={analysis.request_count}, reason={analysis.reason}, "
                    f"decision={decision}"
                )

    assert not failures, "Bundled GraphQL invocations must pass the runtime guard:\n" + "\n".join(
        f"  - {failure}" for failure in failures
    )


def test_graphql_blocks_have_matching_bash_invocations() -> None:
    """Every graphql fenced block must have a gh api graphql bash block in the same section."""
    failures: list[str] = []

    for skill_dir in _all_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")
        skill_name = skill_dir.name

        for section in extract_sections(content):
            section_graphql = extract_graphql_blocks(section)
            if not section_graphql:
                continue

            section_bash = extract_bash_blocks(section)
            section_bash_graphql = [b for b in section_bash if _GH_API_GRAPHQL_RE.search(b)]

            for block in section_graphql:
                if not section_bash_graphql:
                    failures.append(
                        f"{skill_name}: graphql block in section has no "
                        f"'gh api graphql' bash block in the same section"
                    )
                    break

                variable_names = set(re.findall(r"\$([a-zA-Z_]\w*)", block))
                for var in variable_names:
                    flag_found = any(
                        re.search(rf"-[Ff]\s*{re.escape(var)}=", b) for b in section_bash_graphql
                    )
                    if not flag_found:
                        failures.append(
                            f"{skill_name}: graphql variable '${var}' has no "
                            f"'-F {var}=' binding in same-section bash block"
                        )

    assert not failures, (
        "SKILL.md sections with graphql blocks must have concrete "
        "'gh api graphql' bash invocations in the same section:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_prose_graphql_references_have_invocation_blocks() -> None:
    """Step sections with prose GraphQL execution refs must have a bash invocation."""
    failures: list[str] = []

    for skill_dir in _all_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")
        skill_name = skill_dir.name

        for section in extract_sections(content):
            if not has_prose_graphql_execution(section):
                continue

            section_bash = extract_bash_blocks(section)
            if any(_GH_API_GRAPHQL_RE.search(b) for b in section_bash):
                continue

            heading = section.splitlines()[0].strip() if section.strip() else "(no heading)"
            failures.append(
                f"{skill_name}: section '{heading}' references GraphQL execution "
                f"in prose but has no 'gh api graphql' bash block in the same section"
            )

    assert not failures, (
        "SKILL.md sections with prose GraphQL execution references must have "
        "concrete 'gh api graphql' bash invocations in the same section:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_graphql_blocks_use_individual_F_flags_not_json_blob() -> None:
    """gh api graphql invocations must not use -f variables=<json blob> anti-pattern."""
    failures: list[str] = []

    for skill_dir in _all_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")
        bash_blocks = extract_bash_blocks(content)

        for block in bash_blocks:
            if not _GH_API_GRAPHQL_RE.search(block):
                continue

            skill_name = skill_dir.name

            if re.search(r"-[fF]\s+variables=", block) or re.search(
                r"--field\s+variables=", block
            ):
                failures.append(
                    f"{skill_name}: gh api graphql uses '-f variables=' or "
                    f"'--field variables=' (json blob anti-pattern); use individual "
                    f"-F key=value flags instead"
                )

    assert not failures, (
        "gh api graphql invocations must bind variables via individual -F flags, "
        "not a single -f variables=<json blob>:\n" + "\n".join(f"  - {f}" for f in failures)
    )


# --- Synthetic SKILL.md regression tests ---

_GRAPHQL_RULE_ID = "graphql-query-requires-shell-invocation"


def _write_graphql_skill_and_run_rules(tmp_path: Path, skill_md_content: str) -> list[object]:
    skill_dir = tmp_path / "graphql-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(skill_md_content)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        "name: test-recipe\n"
        "kitchen_rules:\n"
        '  - "Use run_skill only."\n'
        "steps:\n"
        "  run_impl:\n"
        "    tool: run_skill\n"
        "    with:\n"
        '      skill_command: "/autoskillit:graphql-skill"\n'
        "    on_success: done\n"
    )
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        return run_semantic_rules(recipe)


class TestGraphqlBlockDetectionBroadened:
    """Verify that graphql blocks without $variable tokens are still caught."""

    def test_angle_bracket_placeholders_without_invocation_fails(self, tmp_path: Path) -> None:
        skill_md = textwrap.dedent("""\
            # graphql-skill

            ## Workflow

            ```graphql
            query {
              repository(owner: "<OWNER>", name: "<REPO>") {
                issues(first: 10) { nodes { title } }
              }
            }
            ```
        """)
        findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
        rule_ids = [f.rule for f in findings]  # type: ignore[union-attr]
        assert _GRAPHQL_RULE_ID in rule_ids

    def test_angle_bracket_placeholders_with_invocation_passes(self, tmp_path: Path) -> None:
        skill_md = textwrap.dedent("""\
            # graphql-skill

            ## Workflow

            ```graphql
            query {
              repository(owner: "<OWNER>", name: "<REPO>") {
                issues(first: 10) { nodes { title } }
              }
            }
            ```

            ```bash
            gh api graphql -f query="$QUERY"
            ```
        """)
        findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
        rule_ids = [f.rule for f in findings]  # type: ignore[union-attr]
        assert _GRAPHQL_RULE_ID not in rule_ids

    def test_field_selection_fragment_without_invocation_fails(self, tmp_path: Path) -> None:
        skill_md = textwrap.dedent("""\
            # graphql-skill

            ## Workflow

            ```graphql
            number title mergedAt
            reviews(first: 100) {
              nodes { author { login } body state }
            }
            ```
        """)
        findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
        rule_ids = [f.rule for f in findings]  # type: ignore[union-attr]
        assert _GRAPHQL_RULE_ID in rule_ids


class TestProseGraphqlDetection:
    """Verify that prose-described GraphQL operations are caught."""

    def test_prose_graphql_without_invocation_fails(self, tmp_path: Path) -> None:
        skill_md = textwrap.dedent("""\
            # graphql-skill

            ## Workflow

            Build batched GraphQL `createIssue` mutations with aliases.
            Execute via `gh api graphql --input -`.
        """)
        findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
        rule_ids = [f.rule for f in findings]  # type: ignore[union-attr]
        assert _GRAPHQL_RULE_ID in rule_ids

    def test_prose_graphql_with_stdin_invocation_fails(self, tmp_path: Path) -> None:
        skill_md = textwrap.dedent("""\
            # graphql-skill

            ## Workflow

            Build batched GraphQL `createIssue` mutations with aliases.

            ```bash
            echo "$MUTATION_JSON" | gh api graphql --input -
            ```
        """)
        findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
        rule_ids = [f.rule for f in findings]  # type: ignore[union-attr]
        assert _GRAPHQL_RULE_ID in rule_ids

    def test_prose_graphql_under_h2_heading_detected(self, tmp_path: Path) -> None:
        """Prose GraphQL under a ## heading (not ### Step N) is caught."""
        content = "# skill\n\n## Workflow\n\nBuild batched GraphQL mutations and execute them.\n"
        sections = extract_sections(content)
        assert any(has_prose_graphql_execution(s) for s in sections)

    def test_both_graphql_block_and_prose_with_invocation_passes(self, tmp_path: Path) -> None:
        content = (
            "# skill\n\n"
            "## Workflow\n\n"
            "Build batched GraphQL mutations.\n\n"
            "```graphql\n"
            "mutation { createIssue(input: {}) { issue { id } } }\n"
            "```\n\n"
            "```bash\n"
            "gh api graphql --input -\n"
            "```\n"
        )
        for section in extract_sections(content):
            section_graphql = extract_graphql_blocks(section)
            section_bash = extract_bash_blocks(section)
            section_bash_graphql = [b for b in section_bash if _GH_API_GRAPHQL_RE.search(b)]
            if section_graphql or has_prose_graphql_execution(section):
                assert section_bash_graphql
