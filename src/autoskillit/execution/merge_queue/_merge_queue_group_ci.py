"""Merge-group CI helpers and GraphQL mutation/query strings — private sub-module.

Extracted from merge_queue.py to satisfy the 500-line size budget.
Import public symbols from autoskillit.execution.merge_queue, not here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from autoskillit.core import get_logger
from autoskillit.execution.merge_queue._merge_queue_classifier import _QUERY_FIELD_MAP

logger = get_logger(__name__)

_QUERY = """
query GetPRAndQueueState($owner: String!, $repo: String!, $prNumber: Int!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      id
      merged
      state
      mergeable
      mergeStateStatus
      autoMergeRequest {
        enabledAt
      }
      statusCheckRollup {
        state
      }
    }
    mergeQueue(branch: $branch) {
      entries(first: 100) {
        nodes {
          pullRequest { number }
          state
        }
      }
    }
  }
}
"""

# Part 2 of the _QUERY_FIELD_MAP validation: checks that every non-computed field
# path head appears as a GraphQL field name in _QUERY.
# Part 1 (keys match PRFetchState) lives in _merge_queue_classifier.py.
for _key, _path in _QUERY_FIELD_MAP.items():
    if _path.startswith("<"):
        continue
    _head = _path.split(".", 1)[0]
    # Word-boundary search prevents "state" from matching inside "mergeStateStatus".
    if not re.search(r"\b" + re.escape(_head) + r"\b", _QUERY):
        raise RuntimeError(
            f"_QUERY is missing GraphQL field {_head!r} required by PRFetchState[{_key!r}]"
        )

_MUTATION_DISABLE_AUTO_MERGE = """
mutation DisableAutoMerge($prId: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $prId}) {
    pullRequest { number }
  }
}
"""

_MUTATION_ENABLE_AUTO_MERGE = """
mutation EnableAutoMerge($prId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(input: {pullRequestId: $prId, mergeMethod: $mergeMethod}) {
    pullRequest { number }
  }
}
"""

_MUTATION_ENQUEUE_PR = """
mutation EnqueuePullRequest($prId: ID!) {
  enqueuePullRequest(input: {pullRequestId: $prId}) {
    mergeQueueEntry { id }
  }
}
"""


async def _query_merge_group_ci(
    repo: str,
    pr_number: int,
    base_branch: str,
    github_token: str | None,
) -> str | None:
    """Query the most recent workflow run on the merge-group ref for this PR.

    Uses `gh run list` with branch prefix matching for the gh-readonly-queue ref.
    Returns 'SUCCESS', 'FAILURE', or None (not found / still running / query failed).
    Never raises.
    """
    branch_prefix = f"gh-readonly-queue/{base_branch}/pr-{pr_number}-"
    env = {**os.environ}
    if github_token:
        env["GH_TOKEN"] = github_token
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--json",
            "conclusion,headBranch",
            "--limit",
            "10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        runs: list[dict[str, str]] = json.loads(stdout.decode())
        for run in runs:
            if run.get("headBranch", "").startswith(branch_prefix):
                conclusion = run.get("conclusion", "")
                if conclusion in ("failure", "cancelled", "timed_out", "action_required"):
                    return "FAILURE"
                if conclusion == "success":
                    return "SUCCESS"
        return None
    except Exception as e:
        logger.warning("_query_merge_group_ci failed: %s", type(e).__name__, exc_info=True)
        return None
