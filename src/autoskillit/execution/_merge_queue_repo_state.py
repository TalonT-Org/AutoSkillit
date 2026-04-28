"""Repository-level merge state helpers extracted from merge_queue.py.

Contains the repo-state GraphQL query, push-trigger detection, and rate-limit
retry logic. Private sub-module — import from autoskillit.execution.merge_queue
for the public API.
"""

from __future__ import annotations

import asyncio
import fnmatch
import random
import re
from typing import Any, Literal

import httpx

from autoskillit.core import YAMLError, get_logger, load_yaml
from autoskillit.execution.github import github_headers

logger = get_logger(__name__)

_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

# Repo-level state query: consolidates three former run_cmd steps into one HTTP round-trip.
# Distinct from _QUERY (PR-level + queue-entries); do not merge these constants.
# Returns: mergeQueue presence, autoMergeAllowed flag, and workflow file texts for
# merge_group trigger detection — all in a single GraphQL call.
_REPO_STATE_QUERY = """
query GetRepoMergeState($owner: String!, $repo: String!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    mergeQueue(branch: $branch) {
      id
    }
    autoMergeAllowed
    object(expression: "HEAD:.github/workflows") {
      ... on Tree {
        entries {
          name
          object {
            ... on Blob {
              text
            }
          }
        }
      }
    }
  }
}
"""

_RATE_LIMIT_MAX_ATTEMPTS = 3
_RATE_LIMIT_SECONDARY_MARKER = "secondary rate limit"


def _text_has_push_trigger(text: str) -> bool:
    """Return True if a workflow file text declares a push trigger.

    Checks for the three YAML forms GitHub supports for a push trigger:
    - ``on: [push, ...]``  (list form)
    - ``on: push``         (scalar form)
    - ``push:``            (mapping form under ``on:``)

    Note: the first two patterns (``on: [push``, ``on: push``) are reliably
    scoped to YAML trigger syntax. The third (``push:``) may match inside
    comments or run-step values — acceptable for the positive-signal-only
    classification heuristic but not precise.
    """
    return any(pat in text for pat in ("on: [push", "on: push", "push:"))


def _push_trigger_applies_to_branch(text: str, branch: str) -> bool:
    """Return True if the workflow push trigger fires for the given branch.

    Parses the YAML to inspect push.branches / push.branches-ignore filters.
    Falls back to presence-only heuristic on YAML parse failure (safe for
    ambiguous/binary blobs). Supports GitHub's fnmatch-compatible glob patterns
    (e.g. 'feature/**', 'release-*').
    """
    try:
        parsed = load_yaml(text)
    except YAMLError:
        return _text_has_push_trigger(text)

    if not isinstance(parsed, dict):
        return False

    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    # Accept both to be safe.
    on_value = parsed.get(True, parsed.get("on"))
    if on_value == "push":
        return True
    if isinstance(on_value, list):
        return "push" in on_value
    if not isinstance(on_value, dict) or "push" not in on_value:
        return False

    push_cfg = on_value["push"]
    if not isinstance(push_cfg, dict) or not push_cfg:
        # push: null or push: {} — no branch filter, fires for all branches
        return True

    branches = push_cfg.get("branches")
    branches_ignore = push_cfg.get("branches-ignore")

    if branches is not None:
        return any(fnmatch.fnmatch(branch, pat) for pat in branches)
    if branches_ignore is not None:
        return not any(fnmatch.fnmatch(branch, pat) for pat in branches_ignore)
    return True


def _has_merge_group_trigger(text: str) -> bool:
    """Return True if the workflow declares a merge_group trigger.

    Parses YAML to inspect the on: key rather than relying on substring
    matching, which can false-positive on comments or shell strings.
    Falls back to substring heuristic on YAML parse failure.
    """
    try:
        parsed = load_yaml(text)
    except YAMLError:
        return "merge_group" in text
    if not isinstance(parsed, dict):
        return False
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    # Accept both to be safe.
    on_value = parsed.get(True, parsed.get("on"))
    if on_value == "merge_group":
        return True
    if isinstance(on_value, list):
        return "merge_group" in on_value
    if isinstance(on_value, dict):
        return "merge_group" in on_value
    return False


def _is_secondary_rate_limit(resp: httpx.Response) -> bool:
    """Return True when a 403 response is a GitHub secondary rate limit.

    GitHub returns HTTP 403 (not 429) for secondary rate limits.
    The response body contains the phrase "secondary rate limit".
    Primary rate limits use HTTP 429 or include x-ratelimit-remaining: 0.
    """
    if resp.status_code != 403:
        return False
    try:
        text = resp.text.lower()
    except Exception:
        logger.warning("Failed to read response body for rate-limit check", exc_info=True)
        return False
    return _RATE_LIMIT_SECONDARY_MARKER in text


def _retry_after_seconds(attempt: int, resp: httpx.Response) -> float:
    """Return seconds to sleep before the next retry attempt.

    Prefers the Retry-After header (integer seconds) when present and valid.
    Falls back to full-jitter exponential backoff: random(0, min(60, 1 * 2^attempt)).
    """
    try:
        header_val = resp.headers.get("Retry-After", "")
        if header_val:
            return float(header_val)
    except (ValueError, AttributeError):
        pass
    return random.uniform(0, min(60.0, 1.0 * (2**attempt)))


async def fetch_repo_merge_state(
    owner: str,
    repo: str,
    branch: str,
    token: str | None,
) -> dict[str, bool | str | None]:
    """Fetch repository merge-state in a single GraphQL round-trip.

    Returns a dict with four keys:
    - ``queue_available``: the branch has an active GitHub merge queue (bool).
    - ``merge_group_trigger``: at least one CI workflow declares the
      ``merge_group`` event trigger (bool).
    - ``auto_merge_available``: the repository has auto-merge enabled (bool).
    - ``ci_event``: ``"push"`` when any workflow declares a push trigger
      that fires for the given branch, or ``None`` otherwise (match-any —
      ci.py scope.event=None lets head_sha provide correctness).

    Null-handling:
    - ``mergeQueue is null`` → ``queue_available: False``  (no queue)
    - ``object is null`` → ``merge_group_trigger: False``, ``ci_event: None``  (no workflows dir)
    - ``entry.object.text is null`` → skip entry (binary/large file)
    - GraphQL ``autoMergeAllowed`` field error (GHES 3.0.x) → ``auto_merge_available: False``

    Only transport-level failures (network timeout, non-200 HTTP status) are
    allowed to propagate; callers are expected to handle them.

    Historical note: Issue #498 ("Merge queue detection should validate workflow has
    merge_group trigger") established the merge_group_trigger field. The ci_event
    field is a closely related extension — verify that the push-trigger scan does
    not regress the merge_group-only detection that #498 established.
    """
    resp: httpx.Response | None = None
    for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
        async with httpx.AsyncClient(
            headers=github_headers(token),
            timeout=30.0,
        ) as client:
            resp = await client.post(
                _GRAPHQL_ENDPOINT,
                json={
                    "query": _REPO_STATE_QUERY,
                    "variables": {"owner": owner, "repo": repo, "branch": branch},
                },
            )
        if resp.status_code == 429 or _is_secondary_rate_limit(resp):
            sleep_secs = _retry_after_seconds(attempt, resp)
            logger.warning(
                "fetch_repo_merge_state rate limited",
                status=resp.status_code,
                attempt=attempt,
                sleep_secs=sleep_secs,
            )
            await asyncio.sleep(sleep_secs)
            continue
        resp.raise_for_status()
        break
    else:
        assert resp is not None, "_RATE_LIMIT_MAX_ATTEMPTS must be >= 1"
        resp.raise_for_status()

    assert resp is not None
    body = resp.json()

    # GitHub GraphQL always returns a JSON object; guard against unexpected shapes.
    if not isinstance(body, dict):
        body = {}

    # Gracefully handle GHES 3.0.x where autoMergeAllowed doesn't exist.
    auto_merge_field_missing = any(
        "autoMergeAllowed" in str(e.get("message", "")) for e in body.get("errors", [])
    )

    repo_data: dict[str, Any] = (body.get("data") or {}).get("repository") or {}
    queue_available = repo_data.get("mergeQueue") is not None
    auto_merge_available = (
        False if auto_merge_field_missing else bool(repo_data.get("autoMergeAllowed", False))
    )

    # Scan workflow files for push and merge_group trigger declarations.
    # Both flags are derived from the same Blob.text scan — no extra round-trips.
    merge_group_trigger = False
    has_push_trigger = False
    workflows_tree = repo_data.get("object")
    if workflows_tree is not None:
        for entry in workflows_tree.get("entries", []):
            blob = entry.get("object") or {}
            text = blob.get("text")
            if text is None:
                continue  # binary or oversized blob — skip
            if _has_merge_group_trigger(text):
                merge_group_trigger = True
            if _push_trigger_applies_to_branch(text, branch):
                has_push_trigger = True

    ci_event: Literal["push"] | None = "push" if has_push_trigger else None

    return {
        "queue_available": queue_available,
        "merge_group_trigger": merge_group_trigger,
        "auto_merge_available": auto_merge_available,
        "ci_event": ci_event,
    }
