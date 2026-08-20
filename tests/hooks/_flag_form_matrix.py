"""Shared parametrize-matrix constants and builders for tests/hooks/ test modules.

Centralizes the reusable ``ids=`` tuples and GraphQL-document command builders
used across multiple ``tests/hooks/`` test files so a flag-form or
GraphQL-delivery/content matrix is defined once and consumed everywhere,
rather than hand-copied per file -- a hand-copied tuple silently drifts the
moment one copy is edited and the others are not. Introduced by
``rectify_github_mutation_guard_argv_parsing_immunity`` Part D (REQ-065),
mirroring the existing ``tests/infra/_pretty_output_helpers.py`` convention
for a private, same-directory test-helper module.
"""

from __future__ import annotations

import json
from pathlib import Path

# A value-taking flag's three distinct syntactic forms this module's tests
# exercise: space-separated (`--flag value` / `-f value`), `=`-joined long
# form (`--flag=value`), and a short flag with its value directly attached,
# no separator (`-fvalue`). Not every flag supports every form (e.g. a flag
# with no short alias has no attached-short form) -- omit inapplicable
# entries at the call site.
#
# The rectify plan that introduced this matrix (REQ-065) named a 4th
# "bundled" form alongside "attached-short" as if they were distinct. They
# are not: the plan's own text (Part B Step 2 item 3 -- "Equals-form... and
# bundled-form (`-fvalue`)") and `_command_classification.py`'s own comments
# (":1721", ":299") both use "bundled" as the *name* for the same
# short-flag-attached-value form already covered here as "attached-short" --
# gh's CLI grammar has no separate 4th syntactic form for a single
# value-taking flag to test. A literal 4th tuple element would duplicate
# "attached-short" under a second label with no new behavior to assert.
FLAG_FORM_MATRIX: tuple[str, ...] = ("space", "equals", "attached-short")

# The four ways a GraphQL document's text can reach `gh api graphql`'s
# `-f query=...`: inline and single-quoted end-to-end, inline and
# double-quoted (or otherwise not fully single-quoted), inline with no
# quoting at all, or read from a file via `--input`.
GRAPHQL_DELIVERY_MATRIX: tuple[str, ...] = (
    "inline-single-quoted",
    "inline-double-quoted",
    "inline-unquoted",
    "input-file",
)

# The four GraphQL-document content shapes this rectify's investigation
# named: ordinary text with no dynamic-shell-looking character, a GraphQL
# `$variable` reference, a GraphQL list literal (`[...]`), and a literal
# shell command-substitution fragment.
GRAPHQL_CONTENT_MATRIX: tuple[str, ...] = (
    "plain",
    "dollar-variable",
    "list-literal",
    "command-substitution",
)

# One representative, whitespace-free document body per GRAPHQL_CONTENT_MATRIX
# entry -- whitespace-free so the same body is valid under every delivery
# form, including "inline-unquoted" (an unquoted argv token ends at the next
# shell whitespace).
GRAPHQL_MATRIX_CONTENT_BODIES: dict[str, str] = {
    "plain": "mutation{addLabels(labelIds:x){clientMutationId}}",
    "dollar-variable": "mutation($id:ID!){addLabels(id:$id){clientMutationId}}",
    "list-literal": "mutation{addLabels(labelIds:[1,2]){clientMutationId}}",
    "command-substitution": "mutation{addLabels(note:`whoami`){clientMutationId}}",
}


def deliver_graphql_document(delivery: str, content_body: str, tmp_path: Path) -> str:
    """Build a `gh api graphql` command delivering *content_body* via *delivery*.

    *delivery* must be a GRAPHQL_DELIVERY_MATRIX value. For "input-file",
    writes *content_body* as the query field of tmp_path/graphql.json and
    returns a command that reads it via `--input graphql.json`; the caller
    must classify the returned command with `cwd=str(tmp_path)` for the
    relative path to resolve.
    """
    if delivery == "inline-single-quoted":
        return f"gh api graphql -f query='{content_body}'"
    if delivery == "inline-double-quoted":
        return f'gh api graphql -f query="{content_body}"'
    if delivery == "inline-unquoted":
        return f"gh api graphql -f query={content_body}"
    if delivery == "input-file":
        (tmp_path / "graphql.json").write_text(
            json.dumps({"query": content_body}), encoding="utf-8"
        )
        return "gh api graphql --input graphql.json"
    raise ValueError(f"unknown delivery form: {delivery!r}")


def graphql_delivery_is_inherently_safe(delivery: str, content: str) -> bool:
    """Whether a (delivery, content) cell should resolve rather than deny.

    True when *delivery* is independently provable "shell could not have
    altered this" (fully single-quoted, or file content -- see
    ``_is_dynamic_shell_value`` and the ``query_from_literal_input`` skip
    condition in ``_command_classification.py``), or when *content* has none
    of ``_DYNAMIC_SHELL_TOKEN_RE``'s trigger characters (``$`` `` ` `` ``*``
    ``?`` ``[``) regardless of quoting.
    """
    return delivery in ("inline-single-quoted", "input-file") or content == "plain"
