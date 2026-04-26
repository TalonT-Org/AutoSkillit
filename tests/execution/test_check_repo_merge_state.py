"""Round-trip budget tests for fetch_repo_merge_state.

Mirrors test_single_graphql_call_per_poll_cycle at the consolidated-tool layer.
All three booleans (queue_available, merge_group_trigger, auto_merge_available)
must come from a single HTTP round-trip to the GitHub GraphQL endpoint.
"""

from __future__ import annotations

import textwrap

import pytest

from autoskillit.execution.merge_queue import fetch_repo_merge_state

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# Reminder: fetch_repo_merge_state now returns ci_event in addition to the
# three boolean fields. Tests below check for the ci_event field explicitly.


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
    assert result["queue_available"] is False
    assert result["merge_group_trigger"] is True
    assert result["auto_merge_available"] is True
    # ci_event: both push and merge_group present — prefer push for historical compat
    assert result["ci_event"] == "push"
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
    assert result["ci_event"] is None  # no parseable trigger in null blobs


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
    assert result["ci_event"] is None  # null object → no workflows → no trigger


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


@pytest.mark.anyio
async def test_check_repo_merge_state_returns_merge_group_as_ci_event(
    httpx_mock, merge_group_only_repo_state
):
    """check_repo_merge_state must derive ci_event='merge_group' when the only
    workflow trigger is merge_group. This capture is what ci_watch reads to avoid
    the event='push' timeout on repos that only trigger on merge_group."""
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json=merge_group_only_repo_state["graphql_response"],
    )
    result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
    assert result["ci_event"] == "merge_group"
    assert result["merge_group_trigger"] is True


@pytest.mark.anyio
async def test_check_repo_merge_state_returns_null_ci_event_for_no_trigger(httpx_mock):
    """When no push or merge_group trigger is found (e.g. schedule-only), ci_event
    must be None — the ci.py default (scope.event=None) matches any trigger."""
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json={
            "data": {
                "repository": {
                    "mergeQueue": None,
                    "autoMergeAllowed": False,
                    "object": {
                        "entries": [
                            {
                                "name": "nightly.yml",
                                "object": {"text": "on:\n  schedule:\n    - cron: '0 0 * * *'"},
                            },
                        ]
                    },
                }
            }
        },
    )
    result = await fetch_repo_merge_state(owner="o", repo="r", branch="main", token=None)
    assert result["ci_event"] is None


def _make_repo_state_response(workflow_entries):
    """Helper: build a mock graphql response with given workflow YAML entries."""
    return {
        "data": {
            "repository": {
                "mergeQueue": None,
                "autoMergeAllowed": True,
                "object": {
                    "entries": [
                        {"name": name, "object": {"text": text}} for name, text in workflow_entries
                    ]
                },
            }
        }
    }


@pytest.mark.anyio
async def test_push_trigger_branch_filter_excludes_feature_branch(httpx_mock):
    """Feature branch not in push.branches must not produce ci_event='push'."""
    workflow_text = textwrap.dedent("""\
        on:
          push:
            branches:
              - main
              - stable
          pull_request:
            branches: [main, integration, stable]
    """)
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json=_make_repo_state_response([("tests.yml", workflow_text)]),
    )
    result = await fetch_repo_merge_state(
        owner="org",
        repo="repo",
        branch="feature/impl-20260401-123456",
        token=None,
    )
    assert result["ci_event"] is None


@pytest.mark.anyio
async def test_push_trigger_no_branch_filter_applies_to_all_branches(httpx_mock):
    """push trigger with no branches: filter → ci_event='push' for any branch."""
    workflow_text = "on:\n  push:\n  pull_request:\n    branches: [main]\n"
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json=_make_repo_state_response([("tests.yml", workflow_text)]),
    )
    result = await fetch_repo_merge_state(
        owner="org",
        repo="repo",
        branch="feature/anything",
        token=None,
    )
    assert result["ci_event"] == "push"


@pytest.mark.anyio
async def test_push_trigger_branch_filter_matches_target_branch(httpx_mock):
    """When queried branch IS in push.branches → ci_event='push'."""
    workflow_text = textwrap.dedent("""\
        on:
          push:
            branches: [main, stable]
    """)
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json=_make_repo_state_response([("tests.yml", workflow_text)]),
    )
    result = await fetch_repo_merge_state(
        owner="org",
        repo="repo",
        branch="main",
        token=None,
    )
    assert result["ci_event"] == "push"


@pytest.mark.anyio
async def test_push_trigger_branches_ignore_allows_feature_branch(httpx_mock):
    """branches-ignore: [main, stable] → push fires on feature branches → ci_event='push'."""
    workflow_text = textwrap.dedent("""\
        on:
          push:
            branches-ignore: [main, stable]
    """)
    httpx_mock.add_response(
        url="https://api.github.com/graphql",
        json=_make_repo_state_response([("tests.yml", workflow_text)]),
    )
    result = await fetch_repo_merge_state(
        owner="org",
        repo="repo",
        branch="feature/impl-20260401",
        token=None,
    )
    assert result["ci_event"] == "push"
