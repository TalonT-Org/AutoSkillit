"""Round-trip budget tests for fetch_repo_merge_state.

Mirrors test_single_graphql_call_per_poll_cycle at the consolidated-tool layer.
All three booleans (queue_available, merge_group_trigger, auto_merge_available)
must come from a single HTTP round-trip to the GitHub GraphQL endpoint.

Tests will FAIL until:
- _REPO_STATE_QUERY constant added to merge_queue.py (Step 2.4)
- fetch_repo_merge_state async function added to merge_queue.py (Step 2.4)
"""

from __future__ import annotations

import pytest

from autoskillit.execution.merge_queue import fetch_repo_merge_state


@pytest.mark.anyio
async def test_check_repo_merge_state_uses_single_graphql_call(httpx_mock):
    """All three booleans come from one HTTP round-trip.

    Mirrors test_single_graphql_call_per_poll_cycle but at the MCP-tool layer.
    """
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json={
            "data": {
                "repository": {
                    "mergeQueue": None,
                    "autoMergeAllowed": True,
                    "object": {
                        "entries": [
                            {
                                "name": "tests.yml",
                                "object": {"text": "on: [push, merge_group]"},
                            },
                        ]
                    },
                }
            }
        },
    )
    result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
    assert result == {
        "queue_available": False,
        "merge_group_trigger": True,
        "auto_merge_available": True,
    }
    assert len(httpx_mock.get_requests()) == 1  # NOT 3, NOT N+2


@pytest.mark.anyio
async def test_check_repo_merge_state_handles_null_blob_text(httpx_mock):
    """Blob.text is null for binary files / files >512KB. Must treat as 'no match'."""
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json={
            "data": {
                "repository": {
                    "mergeQueue": {"id": "MQ_abc"},
                    "autoMergeAllowed": False,
                    "object": {
                        "entries": [
                            {"name": "tests.yml", "object": {"text": None}},
                        ]
                    },
                }
            }
        },
    )
    result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
    assert result["merge_group_trigger"] is False
    assert result["queue_available"] is True


@pytest.mark.anyio
async def test_check_repo_merge_state_handles_missing_workflows_dir(httpx_mock):
    """object is null when .github/workflows does not exist.

    merge_group_trigger must be false, not an error.
    """
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json={
            "data": {
                "repository": {
                    "mergeQueue": None,
                    "autoMergeAllowed": False,
                    "object": None,
                }
            }
        },
    )
    result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
    assert result["merge_group_trigger"] is False
    assert result["queue_available"] is False


def test_repo_state_query_is_distinct_module_constant_from_pr_state_query():
    """_REPO_STATE_QUERY and _QUERY must be distinct module-level constants.

    Prevents a future DRY refactor from merging them (they operate on different
    scopes: repo-level vs. PR-level).
    """
    from autoskillit.execution import merge_queue

    assert hasattr(merge_queue, "_REPO_STATE_QUERY")
    assert hasattr(merge_queue, "_QUERY")
    assert merge_queue._REPO_STATE_QUERY is not merge_queue._QUERY
    assert "mergeQueue(branch" in merge_queue._REPO_STATE_QUERY
    assert "autoMergeAllowed" in merge_queue._REPO_STATE_QUERY
    assert "object(expression:" in merge_queue._REPO_STATE_QUERY
