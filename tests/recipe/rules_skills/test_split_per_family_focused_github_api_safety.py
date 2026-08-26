"""Per-family focused tests for GitHub-API-safety SKILL.md semantic rules.

Covers the three `@semantic_rule` checks that live in
`rules_skill_content_github_api_safety.py`:

  - skill-no-issue-comments
  - reviews-post-requires-input-flag
  - graphql-query-requires-shell-invocation

These tests were relocated verbatim from `tests/recipe/test_rules_skill_content.py`
as part of the #4852 decomposition; no test bodies were edited.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.core import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.rules_skills._helpers import (
    make_recipe_for_skill,
    write_skill_and_run_rules,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# reviews-post-requires-input-flag
# ---------------------------------------------------------------------------

_REVIEWS_RULE_ID = "reviews-post-requires-input-flag"


def test_reviews_post_requires_input_flag_rule(tmp_path: Path) -> None:
    """Rule fires when a SKILL.md section mentions reviews POST but lacks --input -."""
    skill_dir = tmp_path / "test-reviews-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # test-reviews-skill

            ### Step 1
            Post the findings:
            gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \\
              --method POST \\
              --field event=COMMENT
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("test-reviews-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    rule_ids = [f.rule for f in findings]
    assert _REVIEWS_RULE_ID in rule_ids, (
        f"Expected '{_REVIEWS_RULE_ID}' finding when reviews POST lacks --input -, got: {rule_ids}"
    )
    matching = [f for f in findings if f.rule == _REVIEWS_RULE_ID]
    assert all(f.severity == Severity.ERROR for f in matching)


def test_reviews_post_requires_input_flag_rule_passes_with_input_flag(
    tmp_path: Path,
) -> None:
    """Rule does NOT fire when --input - is present in the same section as the reviews POST."""
    skill_dir = tmp_path / "good-reviews-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # good-reviews-skill

            ### Step 1
            Post the findings via stdin:
            jq -n --arg body "summary" --arg event COMMENT --argjson comments "$C" \\
              '{body: $body, event: $event, comments: $comments}' | \\
            gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \\
              --method POST --input -
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("good-reviews-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    rule_ids = [f.rule for f in findings]
    assert _REVIEWS_RULE_ID not in rule_ids, (
        f"Rule must not fire when --input - is present alongside the reviews POST, got: {rule_ids}"
    )


def test_reviews_post_rule_subsection_granularity(tmp_path: Path) -> None:
    """Rule uses ### granularity: --input - in a different ### step does not satisfy the guard.

    If the implementation mistakenly used ## granularity (extract_sections), --input - from
    any step in the same ## Workflow section would suppress the finding. This test catches
    that false-negative regression.
    """
    skill_dir = tmp_path / "granularity-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # granularity-skill

            ## Workflow

            ### Step A
            Do something with --input - for an unrelated purpose.
            gh api /repos/{owner}/{repo}/something --method POST --input -

            ### Step B
            Post the findings:
            gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \\
              --method POST \\
              --field event=COMMENT
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("granularity-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    rule_ids = [f.rule for f in findings]
    assert _REVIEWS_RULE_ID in rule_ids, (
        f"Rule must fire when --input - is in a different ### step than the reviews POST "
        f"(### granularity required, not ## granularity). Got findings: {rule_ids}"
    )


def test_reviews_post_regex_flag_before_path(tmp_path: Path) -> None:
    """Rule fires when --method POST appears before the endpoint path (flag-before-path form)."""
    skill_dir = tmp_path / "flag-before-path-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            # flag-before-path-skill

            ### Step 1
            Post the review:
            gh api --method POST repos/{owner}/{repo}/pulls/{pr_number}/reviews \\
              --field event=COMMENT
            """
        )
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("flag-before-path-skill", {}))
    recipe = load_recipe(recipe_path)
    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)
    rule_ids = [f.rule for f in findings]
    assert _REVIEWS_RULE_ID in rule_ids, (
        f"Rule must fire for 'gh api --method POST URL' form (flag before path). "
        f"Got findings: {rule_ids}"
    )


# ---------------------------------------------------------------------------
# graphql-query-requires-shell-invocation tests
# ---------------------------------------------------------------------------

_GRAPHQL_RULE_ID = "graphql-query-requires-shell-invocation"


def _write_graphql_skill_and_run_rules(tmp_path: Path, skill_md_content: str):
    return write_skill_and_run_rules(tmp_path, skill_md_content, skill_name="graphql-skill")


def test_graphql_rule_fires_when_no_bash_invocation(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ### Step 1
        ```graphql
        query($owner:String!, $repo:String!, $number:Int!) {
          repository(owner:$owner, name:$repo) {
            pullRequest(number:$number) { title }
          }
        }
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_does_not_fire_when_bash_invocation_present(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ### Step 1
        ```graphql
        query($owner:String!, $repo:String!, $number:Int!) {
          repository(owner:$owner, name:$repo) {
            pullRequest(number:$number) { title }
          }
        }
        ```

        ```bash
        gh api graphql \\
          -f query='...' \\
          -F owner="$OWNER" \\
          -F repo="$REPO" \\
          -F number=$NUMBER
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID not in rule_ids


def test_graphql_rule_fires_for_case_mismatched_variable_bindings(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ### Step 1
        ```graphql
        query($owner:String!, $repo:String!, $number:Int!) {
          repository(owner:$owner, name:$repo) {
            pullRequest(number:$number) { title }
          }
        }
        ```

        ```bash
        gh api graphql \\
          -f query='...' \\
          -F OWNER="$OWNER" \\
          -F REPO="$REPO" \\
          -F NUMBER=$NUMBER
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_fires_for_fragment_without_same_section_invocation(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ### Step 1
        ```graphql
        number title mergedAt
        reviews(first: 100) {
          nodes { author { login } body state submittedAt }
        }
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_does_not_fire_for_non_parameterized_block_with_invocation(
    tmp_path: Path,
) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ### Step 1
        ```graphql
        number title mergedAt
        reviews(first: 100) {
          nodes { author { login } body state submittedAt }
        }
        ```

        ```bash
        gh api graphql -f query="$BATCH_QUERY"
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID not in rule_ids


def test_graphql_rule_fires_for_prose_without_same_section_invocation(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Workflow

        Build batched GraphQL `createIssue` mutations with aliases.
        Execute via `gh api graphql --input -`.
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_fires_for_prose_with_stdin_invocation(
    tmp_path: Path,
) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Workflow

        Build batched GraphQL `createIssue` mutations with aliases.

        ```bash
        echo "$MUTATION_JSON" | gh api graphql --input -
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


@pytest.mark.parametrize(
    "bash_block",
    [
        'gh api graphql --input "/absolute/run/create.json"',
        'echo "/absolute/run/create.json.bak"\ngh api graphql --input "/absolute/run/create.json"',
    ],
    ids=["literal-path", "longer-earlier-token"],
)
def test_graphql_rule_accepts_literal_payload_with_variables_object(
    tmp_path: Path,
    bash_block: str,
) -> None:
    import json as _json

    query = (
        "mutation Create($repo: ID!, $title: String!) { "
        "createIssue(input: {repositoryId: $repo, title: $title}) { issue { id } } }"
    )
    skill_md = (
        textwrap.dedent(
            """\
        # graphql-skill

        ## Workflow

        Use a file-write tool in a separate completed tool call before invoking the payload.

        ```graphql
        mutation Create($repo: ID!, $title: String!) {
          createIssue(input: {repositoryId: $repo, title: $title}) { issue { id } }
        }
        ```

        ```json
        {
          "query": <QUERY_JSON>,
          "variables": {"repo": "R_1", "title": "Issue"}
        }
        ```

        ```bash
        <BASH_BLOCK>
        ```
        """
        )
        .replace("<QUERY_JSON>", _json.dumps(query))
        .replace("<BASH_BLOCK>", bash_block)
    )

    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]

    assert _GRAPHQL_RULE_ID not in rule_ids


@pytest.mark.parametrize(
    "bash_block",
    [
        'echo "$PAYLOAD" | gh api graphql --input -',
        'QUERY="mutation { deleteIssue(input: {}) { clientMutationId } }"\n'
        'gh api graphql -f query="$QUERY"',
        'echo \'{"query":"mutation { deleteIssue(input: {}) { clientMutationId } }"}\' '
        "> /absolute/run/payload.json && "
        'gh api graphql --input "/absolute/run/payload.json"',
        "cp /absolute/run/source.json /absolute/run/payload.json && "
        'gh api graphql --input "/absolute/run/payload.json"',
    ],
    ids=["stdin", "shell-query", "same-command-write", "same-command-copy"],
)
def test_graphql_rule_rejects_unsafe_mutation_shapes(
    tmp_path: Path,
    bash_block: str,
) -> None:
    skill_md = textwrap.dedent(
        f"""\
        # graphql-skill

        ## Workflow

        Build a GraphQL mutation, using a separate completed tool call when a file is needed.

        ```bash
        {bash_block}
        ```
        """
    )

    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]

    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_accepts_fully_literal_inline_mutation(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Workflow

        ```bash
        gh api graphql \
          -f query='mutation { deleteIssue(input: {issueId: "I_1"}) { clientMutationId } }'
        ```
        """
    )

    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]

    assert _GRAPHQL_RULE_ID not in rule_ids


def test_graphql_rule_rejects_generated_named_mutation_with_dynamic_query(
    tmp_path: Path,
) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Workflow

        Generate aliased GraphQL `deleteIssue` operations.

        ```bash
        gh api graphql -f query="$QUERY"
        ```
        """
    )

    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]

    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_does_not_bind_variables_from_json_without_query(
    tmp_path: Path,
) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Workflow

        Use a file-write tool in a separate completed tool call before invoking the payload.

        ```graphql
        mutation Delete($issue: ID!) {
          deleteIssue(input: {issueId: $issue}) { clientMutationId }
        }
        ```

        ```json
        {"variables": {"issue": "I_1"}}
        ```

        ```bash
        gh api graphql --input "/absolute/run/delete.json"
        ```
        """
    )

    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]

    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_rejects_single_variables_blob(tmp_path: Path) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Workflow

        ```graphql
        query($owner: String!) { repository(owner: $owner, name: "repo") { id } }
        ```

        ```bash
        gh api graphql \
          -f query='query($owner: String!) { repository(owner: $owner, name: "repo") { id } }' \
          -f variables='{"owner":"o"}'
        ```
        """
    )

    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]

    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_fires_for_prose_in_different_section_than_invocation(
    tmp_path: Path,
) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Step 5

        Build batched GraphQL `createIssue` mutations with aliases.

        ## Step 6

        ```bash
        echo "$MUTATION_JSON" | gh api graphql --input -
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


def test_graphql_rule_fires_for_documentation_schema_reference_without_invocation(
    tmp_path: Path,
) -> None:
    skill_md = textwrap.dedent(
        """\
        # graphql-skill

        ## Schema Reference

        ```graphql
        type PullRequest {
          title: String!
          mergedAt: DateTime
          reviews(first: Int): ReviewConnection!
        }
        ```
        """
    )
    findings = _write_graphql_skill_and_run_rules(tmp_path, skill_md)
    rule_ids = [f.rule for f in findings]
    assert _GRAPHQL_RULE_ID in rule_ids


# ---------------------------------------------------------------------------
# skill-no-issue-comments tests
# ---------------------------------------------------------------------------

_GH_ISSUE_COMMENT_RULE_ID = "skill-no-issue-comments"


def test_skill_no_issue_comments_fires_for_gh_issue_comment_invocation(
    tmp_path: Path,
) -> None:
    """Rule fires when a SKILL.md bash block uses 'gh issue comment'."""
    skill_md = textwrap.dedent(
        """\
        # gh-comment-skill

        ### Step 1
        ```bash
        gh issue comment 123 --body "thanks for the review"
        ```
        """
    )
    findings = write_skill_and_run_rules(tmp_path, skill_md, skill_name="gh-comment-skill")
    rule_ids = [f.rule for f in findings]
    assert _GH_ISSUE_COMMENT_RULE_ID in rule_ids, (
        f"Expected '{_GH_ISSUE_COMMENT_RULE_ID}' finding for 'gh issue comment' invocation, "
        f"got: {rule_ids}"
    )
    matching = [f for f in findings if f.rule == _GH_ISSUE_COMMENT_RULE_ID]
    assert all(f.severity == Severity.ERROR for f in matching)


def test_skill_no_issue_comments_does_not_fire_for_gh_issue_edit(tmp_path: Path) -> None:
    """Rule does NOT fire for `gh issue edit --body-file`, the correct replacement."""
    skill_md = textwrap.dedent(
        """\
        # gh-edit-skill

        ### Step 1
        ```bash
        gh issue edit 123 --body-file /tmp/updated_body.md
        ```
        """
    )
    findings = write_skill_and_run_rules(tmp_path, skill_md, skill_name="gh-edit-skill")
    rule_ids = [f.rule for f in findings]
    assert _GH_ISSUE_COMMENT_RULE_ID not in rule_ids, (
        f"Rule must not fire for 'gh issue edit --body-file', got: {rule_ids}"
    )
