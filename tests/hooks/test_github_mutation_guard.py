"""Behavioral tests for the raw GitHub mutation PreToolUse guard."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from .conftest import _RUN_CMD_TOOL_DIRECT, make_hook_event

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_RUN_CMD_TOOL = "mcp__plugin_autoskillit_autoskillit__run_cmd"
_REVIEW_ROUTE = "/repos/o/r/pulls/7/reviews"
_OTHER_ROUTE = "/repos/o/r/issues/7/comments"


def _bash_event(command: str, *, cwd: str | None = None) -> dict:
    event: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        event["cwd"] = cwd
    return event


def _run_cmd_event(command: str, *, cwd: str | None = None) -> dict:
    tool_input: dict = {"cmd": command}
    if cwd is not None:
        tool_input["cwd"] = cwd
    return {"tool_name": _RUN_CMD_TOOL, "tool_input": tool_input}


def _run_hook(event: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    from autoskillit.hooks.guards.github_mutation_guard import main

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            main()
    except SystemExit:
        pass
    rendered = stdout.getvalue().strip()
    return json.loads(rendered) if rendered else {}


def _decision(event: dict, monkeypatch: pytest.MonkeyPatch) -> str | None:
    result = _run_hook(event, monkeypatch)
    if not result:
        return None
    return result["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize(
    "command",
    [
        "gh pr review 7 --comment --body 'review body'",
        f"gh api --method POST {_REVIEW_ROUTE} -f event=COMMENT",
        f"gh api -X POST {_REVIEW_ROUTE} -f event=APPROVE",
        (
            f"curl --request POST https://api.github.com{_REVIEW_ROUTE} "
            '-d \'{"event":"COMMENT"}\''
        ),
        f"/usr/bin/gh api --method POST {_REVIEW_ROUTE} -f event=COMMENT",
        f"/usr/bin/curl -X POST https://api.github.com{_REVIEW_ROUTE} -d '{{}}'",
        f"curl -d'{{}}' https://api.github.com{_REVIEW_ROUTE}",
        "gh api --method POST /repos/o/r/pulls/7/comments/99/replies -f body=x",
    ],
    ids=[
        "gh-pr-review",
        "gh-api-method",
        "gh-api-short-method",
        "curl-request",
        "absolute-gh",
        "absolute-curl",
        "attached-curl-data",
        "review-reply",
    ],
)
@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_every_direct_rest_review_mutation_is_denied(
    command: str,
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        (
            f"gh api --method PATCH /repos/o/r/issues/7 -f title=x && "
            f"gh api --method POST {_REVIEW_ROUTE} -f event=COMMENT"
        ),
        (
            f"gh api --method PATCH /repos/o/r/issues/7 -f title=x\n"
            f"gh api --method POST {_REVIEW_ROUTE} -f event=COMMENT"
        ),
        (
            "gh api --method PATCH /repos/o/r/issues/7 -f title=x; "
            "gh api --method DELETE /repos/o/r/issues/8"
        ),
    ],
    ids=["and-join", "newline-join", "two-non-review-mutations"],
)
@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_multiple_mutations_are_denied(
    command: str,
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        f"for n in 1 2; do gh api --method POST {_REVIEW_ROUTE}; done",
        f"post_review() {{ gh api --method POST {_REVIEW_ROUTE}; }}; post_review",
        f"bash -c 'gh api --method POST {_REVIEW_ROUTE}'",
        f"sh -c 'curl -X POST https://api.github.com{_REVIEW_ROUTE}'",
        f"eval 'gh api --method POST {_REVIEW_ROUTE}'",
        f"printf '%s\\n' {_REVIEW_ROUTE} | xargs -n1 gh api --method POST",
        (
            'python3 -c "import subprocess; '
            f"subprocess.run(['gh','api','--method','POST','{_REVIEW_ROUTE}'])\""
        ),
        (
            'python3 -c "import os; '
            f"os.system('curl -X POST https://api.github.com{_REVIEW_ROUTE}')\""
        ),
    ],
    ids=[
        "loop",
        "function",
        "nested-bash",
        "nested-sh",
        "eval",
        "xargs",
        "python-subprocess",
        "python-os-system",
    ],
)
@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_wrappers_and_repeatable_shell_constructs_cannot_bypass_guard(
    command: str,
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        'gh api --method "$METHOD" /repos/o/r/issues/7 -f title=x',
        'gh api --method POST "$ROUTE" -f title=x',
        "curl -X \"$METHOD\" https://api.github.com/repos/o/r/issues/7 -d '{}'",
        "curl -X POST \"https://api.github.com${ROUTE}\" -d '{}'",
        "gh api --method",
    ],
    ids=[
        "dynamic-gh-method",
        "dynamic-gh-route",
        "dynamic-curl-method",
        "dynamic-curl-route",
        "missing-method-value",
    ],
)
@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_dynamic_or_unresolved_mutations_fail_closed(
    command: str,
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


@pytest.mark.parametrize(
    "document",
    [
        'mutation { addPullRequestReview(input:{pullRequestId:"PR"}) { clientMutationId } }',
        (
            "mutation { submitPullRequestReview("
            'input:{pullRequestReviewId:"R",event:COMMENT}) { clientMutationId } }'
        ),
        (
            "mutation { addPullRequestReviewComment("
            'input:{pullRequestReviewId:"R",body:"x"}) { clientMutationId } }'
        ),
    ],
    ids=[
        "add-review",
        "submit-review",
        "add-review-comment",
    ],
)
@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_graphql_review_mutations_are_denied(
    document: str,
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command = f"gh api graphql -f query={json.dumps(document)}"

    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_graphql_thread_resolution_remains_a_proven_nonpublication_mutation(
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = 'mutation { resolveReviewThread(input:{threadId:"T"}) { thread { isResolved } } }'
    command = f"gh api graphql -f query={json.dumps(document)}"

    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) is None


@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_literal_review_input_file_is_still_denied(
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "review.json"
    payload.write_text(
        json.dumps(
            {
                "event": "COMMENT",
                "body": "summary",
                "comments": [
                    {"path": "a.py", "line": 2, "side": "RIGHT", "body": "one"},
                    {"path": "b.py", "line": 4, "side": "RIGHT", "body": "two"},
                ],
            }
        ),
        encoding="utf-8",
    )
    command = f"gh api --method POST {_REVIEW_ROUTE} --input review.json"

    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_prior_literal_input_rewrite_fails_closed(
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "payload.json").write_text(json.dumps({"body": "before"}), encoding="utf-8")
    command = (
        "printf '%s' '{\"body\":\"after\"}' > payload.json && "
        f"gh api --method POST {_OTHER_ROUTE} --input payload.json"
    )

    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) == "deny"


def test_regular_non_symlink_json_object_preserves_single_non_review_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = tmp_path / "comment.json"
    payload.write_text(json.dumps({"body": "one comment"}), encoding="utf-8")
    command = f"gh api --method POST {_OTHER_ROUTE} --input comment.json"

    assert _decision(_run_cmd_event(command, cwd=str(tmp_path)), monkeypatch) is None


@pytest.mark.parametrize(
    "payload_kind",
    ["stdin", "malformed", "missing", "oversized", "symlink", "non-object"],
)
def test_untrusted_gh_input_payloads_fail_closed(
    payload_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_arg = "-"
    if payload_kind == "malformed":
        (tmp_path / "payload.json").write_text("{not-json", encoding="utf-8")
        input_arg = "payload.json"
    elif payload_kind == "missing":
        input_arg = "missing.json"
    elif payload_kind == "oversized":
        (tmp_path / "payload.json").write_bytes(b"x" * (1024 * 1024 + 1))
        input_arg = "payload.json"
    elif payload_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        (tmp_path / "payload.json").symlink_to(target)
        input_arg = "payload.json"
    elif payload_kind == "non-object":
        (tmp_path / "payload.json").write_text("[]", encoding="utf-8")
        input_arg = "payload.json"

    command = f"gh api --method POST {_OTHER_ROUTE} --input {input_arg}"

    assert _decision(_run_cmd_event(command, cwd=str(tmp_path)), monkeypatch) == "deny"


def test_relative_input_file_without_explicit_cwd_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "payload.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")
    command = f"gh api --method POST {_OTHER_ROUTE} --input payload.json"

    assert _decision(_run_cmd_event(command), monkeypatch) == "deny"


@pytest.mark.parametrize(
    "command",
    [
        f"echo 'gh api --method POST {_REVIEW_ROUTE}'",
        f"printf '%s\\n' 'curl -X POST https://api.github.com{_REVIEW_ROUTE}'",
        "gh api /repos/o/r/pulls/7/reviews",
        "curl https://api.github.com/repos/o/r/pulls/7/reviews",
        "gh pr view 7 --json reviews",
    ],
    ids=["quoted-echo", "quoted-printf", "gh-read", "curl-read", "gh-pr-view"],
)
@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_inert_mentions_and_read_only_commands_are_allowed(
    command: str,
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) is None


@pytest.mark.parametrize("event_factory", [_bash_event, _run_cmd_event], ids=["bash", "run-cmd"])
def test_proven_single_non_review_mutation_is_preserved(
    event_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command = "gh api --method PATCH /repos/o/r/issues/7 -f title=updated"

    assert _decision(event_factory(command, cwd=str(tmp_path)), monkeypatch) is None


@pytest.mark.parametrize(
    "case_id",
    [
        "fp1-run-cmd-pwd-differing-cwds",
        "fp2-run-cmd-git-rev-parse",
        "fp3-run-cmd-sed",
        "fp4-run-cmd-pwd-tool-cwd-omitted",
        "fp5-run-cmd-pwd-equal-cwds",
        "fp6-bash-chained-benign",
        "fp7-bash-benign-loop",
        "fp8-bash-dynamic-echo",
        "fp8-bash-dynamic-git-log",
        "fp9-bash-gh-in-quoted-loop-string",
        "fp10-run-cmd-source-then-gh-read",
    ],
)
def test_false_positive_corpus_is_allowed(
    case_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reproduce the live incident: a worktree run_cmd cwd must not be denied.

    Every case here previously denied (or would deny under a content-free
    envelope-equality check) despite issuing no GitHub mutation at all.
    """
    repo = str(tmp_path / "repo")
    worktree = str(tmp_path / "worktree")

    if case_id == "fp1-run-cmd-pwd-differing-cwds":
        event = make_hook_event(tool="run_cmd", command="pwd", tool_cwd=worktree, payload_cwd=repo)
    elif case_id == "fp2-run-cmd-git-rev-parse":
        event = make_hook_event(
            tool="run_cmd",
            command="git rev-parse --show-toplevel 2>&1 | head -c 2000",
            tool_cwd=worktree,
            payload_cwd=repo,
        )
    elif case_id == "fp3-run-cmd-sed":
        event = make_hook_event(
            tool="run_cmd",
            command="sed -n '1,50p' plan.md",
            tool_cwd=worktree,
            payload_cwd=repo,
        )
    elif case_id == "fp4-run-cmd-pwd-tool-cwd-omitted":
        event = make_hook_event(tool="run_cmd", command="pwd", tool_cwd=None, payload_cwd=repo)
    elif case_id == "fp5-run-cmd-pwd-equal-cwds":
        event = make_hook_event(tool="run_cmd", command="pwd", tool_cwd=repo, payload_cwd=repo)
    elif case_id == "fp6-bash-chained-benign":
        event = make_hook_event(tool="Bash", command="ls && pwd", payload_cwd=repo)
    elif case_id == "fp7-bash-benign-loop":
        event = make_hook_event(tool="Bash", command="for n in 1 2; do ls; done", payload_cwd=repo)
    elif case_id == "fp8-bash-dynamic-echo":
        event = make_hook_event(tool="Bash", command='echo "$X"', payload_cwd=repo)
    elif case_id == "fp8-bash-dynamic-git-log":
        event = make_hook_event(tool="Bash", command='git log "$REF"', payload_cwd=repo)
    elif case_id == "fp9-bash-gh-in-quoted-loop-string":
        event = make_hook_event(
            tool="Bash",
            command='for f in *; do echo "see gh docs"; done',
            payload_cwd=repo,
        )
    elif case_id == "fp10-run-cmd-source-then-gh-read":
        event = make_hook_event(
            tool="run_cmd",
            command="source .venv/bin/activate && gh pr view --json state",
            tool_cwd=worktree,
            payload_cwd=repo,
        )
    else:
        raise AssertionError(f"unhandled false-positive corpus case: {case_id}")

    assert _decision(event, monkeypatch) is None


def test_false_positive_corpus_allows_direct_prefix_run_cmd_tool_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Codex's direct-prefix run_cmd tool name must satisfy the same matcher."""
    event = make_hook_event(
        tool="run_cmd",
        command="pwd",
        tool_cwd=str(tmp_path / "worktree"),
        payload_cwd=str(tmp_path / "repo"),
        run_cmd_tool_name=_RUN_CMD_TOOL_DIRECT,
    )

    assert _decision(event, monkeypatch) is None


@pytest.mark.parametrize("surface", ["bash", "run-cmd"])
def test_conflicting_cwd_authorities_fail_closed(
    surface: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    command = "gh api --method PATCH /repos/o/r/issues/7 -f title=updated"
    if surface == "bash":
        event = _bash_event(command, cwd=str(tmp_path))
        event["tool_input"]["cwd"] = str(other)
    else:
        event = _run_cmd_event(command, cwd=str(tmp_path))
        event["cwd"] = str(other)

    assert _decision(event, monkeypatch) == "deny"


def test_guard_registration_is_exact() -> None:
    from autoskillit.hook_registry import HOOK_REGISTRY, NEW_SUBDIR_BASENAMES

    entries = [hook for hook in HOOK_REGISTRY if "guards/github_mutation_guard.py" in hook.scripts]

    assert len(entries) == 1
    entry = entries[0]
    assert entry.event_type == "PreToolUse"
    assert entry.matcher == r"Bash|mcp__.*autoskillit.*__run_cmd"
    assert entry.mechanism == "deny"
    assert entry.codex_status == "works-as-is"
    assert entry.enforcement_strength == {
        "claude_code": "hard",
        "codex": "works-as-is",
    }
    assert "github_mutation_guard.py" in NEW_SUBDIR_BASENAMES
