"""Tests for the token_summary_appender PostToolUse hook.

The hook appends a ## Token Usage Summary table to newly-opened PRs by
reading on-disk session logs after every run_skill response.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    log_root: Path | None = None,
    hook_config_path: Path | None = None,
) -> tuple[str, int]:
    """Run token_summary_appender.main() with synthetic stdin.

    Returns (stdout_output, exit_code).

    Args:
        hook_config_path: Path to a hook config JSON file containing ``kitchen_id``
            (or legacy ``pipeline_id``).
            When provided, patches ``_read_kitchen_id`` to return the ``kitchen_id``
            value from that file. When absent, ``_read_kitchen_id`` reads from the
            real filesystem (returns '' if no file present in the test CWD).
    """
    from autoskillit.hooks.token_summary_hook import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})
    exit_code = 0
    buf = io.StringIO()

    with ExitStack() as stack:
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
        stack.enter_context(redirect_stdout(buf))
        if log_root is not None:
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_hook._log_root",
                    return_value=log_root,
                )
            )
        if hook_config_path is not None:
            cfg_data = json.loads(hook_config_path.read_text(encoding="utf-8"))
            kitchen_id = cfg_data.get("kitchen_id") or cfg_data.get("pipeline_id", "")
            stack.enter_context(
                patch(
                    "autoskillit.hooks.token_summary_hook._read_kitchen_id",
                    return_value=kitchen_id,
                )
            )
        try:
            main()
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0

    return buf.getvalue(), exit_code


def _make_run_skill_event(result_text: str = "Done.\n%%ORDER_UP%%") -> dict:
    """Create a double-wrapped PostToolUse event for run_skill."""
    inner = {"result": result_text, "success": True}
    outer = {"result": json.dumps(inner)}
    return {
        "tool_name": "mcp__autoskillit_server__run_skill",
        "tool_response": json.dumps(outer),
    }


def _write_sessions(log_root: Path, entries: list[dict]) -> None:
    """Write sessions.jsonl and token_usage.json files for test setup."""
    (log_root / "sessions.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    for entry in entries:
        dir_name = entry["dir_name"]
        session_dir = log_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        token_data = {
            "step_name": entry.get("step_name", "unknown"),
            "input_tokens": entry.get("input_tokens", 1000),
            "output_tokens": entry.get("output_tokens", 500),
            "cache_creation_input_tokens": entry.get("cache_creation_input_tokens", 100),
            "cache_read_input_tokens": entry.get("cache_read_input_tokens", 200),
            "timing_seconds": entry.get("timing_seconds", 10.0),
        }
        (session_dir / "token_usage.json").write_text(json.dumps(token_data))


# ---------------------------------------------------------------------------
# TSA-1: hook script exists on disk
# ---------------------------------------------------------------------------


def test_tsa1_token_summary_appender_script_exists() -> None:
    """token_summary_hook.py must exist in hooks/ on disk."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "hooks" / "token_summary_hook.py").exists()


def test_tsa_rest_api_no_gh_pr_commands() -> None:
    """Hook source must not contain 'gh pr edit' or 'gh pr view' subprocess calls.

    REQ-TEST-001: verifies both read and write operations use gh api (REST).
    """
    from autoskillit.core.paths import pkg_root

    source = (pkg_root() / "hooks" / "token_summary_hook.py").read_text(encoding="utf-8")
    assert "gh pr edit" not in source, (
        "gh pr edit found in hook — must be replaced with "
        "gh api repos/.../pulls/{N} --method PATCH --field body=..."
    )
    assert "gh pr view" not in source, (
        "gh pr view found in hook — must be replaced with gh api repos/.../pulls/{N} --jq '.body'"
    )


# ---------------------------------------------------------------------------
# TSA-2: no PR URL in result → exits 0, makes no gh calls
# ---------------------------------------------------------------------------


def test_tsa2_no_pr_url_exits_zero() -> None:
    """No GitHub PR URL in tool result → exits 0, no gh subprocess."""
    from autoskillit.core.paths import pkg_root

    hook_path = pkg_root() / "hooks" / "token_summary_hook.py"
    event = _make_run_skill_event("done.\n%%ORDER_UP%%")
    stdin_text = json.dumps(event)

    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert "gh" not in proc.stdout


# ---------------------------------------------------------------------------
# TSA-3: sessions.jsonl does not exist → exits 0 silently
# ---------------------------------------------------------------------------


def test_tsa3_no_sessions_jsonl_exits_zero(tmp_path: Path) -> None:
    """Missing sessions.jsonl → exits 0 (valid: no sessions yet)."""
    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    _, exit_code = _run_hook(event, log_root=tmp_path / "nonexistent")
    assert exit_code == 0


# ---------------------------------------------------------------------------
# TSA-4: no pipeline_id → sessions skipped → exits 0 silently
# ---------------------------------------------------------------------------


def test_tsa4_no_pipeline_id_sessions_exits_zero(tmp_path: Path) -> None:
    """sessions.jsonl exists but no pipeline_id set → hook skips all sessions → exits 0."""
    log_root = tmp_path / "logs"
    log_root.mkdir()

    _write_sessions(
        log_root,
        [
            {"dir_name": "s1", "cwd": "/some/other/pipeline", "step_name": "plan"},
        ],
    )

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    _, exit_code = _run_hook(event, log_root=log_root)
    assert exit_code == 0


# ---------------------------------------------------------------------------
# TSA-5: matching sessions → formats table and calls gh pr edit
# ---------------------------------------------------------------------------


def test_tsa5_matching_sessions_formats_table_and_edits_pr(tmp_path: Path) -> None:
    """Matching sessions → aggregate token data and append ## Token Usage Summary."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa5"

    # 3 sessions: plan-1, plan-2, open-pr
    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "plan-1",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "timing_seconds": 10.0,
            },
            {
                "dir_name": "session-2",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "plan-2",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "timing_seconds": 10.0,
            },
            {
                "dir_name": "session-3",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "open-pr",
                "input_tokens": 500,
                "output_tokens": 250,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 100,
                "timing_seconds": 5.0,
            },
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "Existing PR body without summary."

    edit_calls: list[list[str]] = []

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(list(args))
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1
    field_idx = edit_calls[0].index("--raw-field")
    body_arg = edit_calls[0][field_idx + 1]
    assert body_arg.startswith("body=")
    body_content = body_arg[len("body=") :]
    assert "## Token Usage Summary" in body_content
    # plan-1 and plan-2 should collapse to "plan"
    assert "plan" in body_content
    # open-pr should be preserved
    assert "open-pr" in body_content
    # Total row
    assert "**Total**" in body_content
    # 4-column token headers
    assert "uncached" in body_content
    assert "cache_read" in body_content
    assert "cache_write" in body_content


# ---------------------------------------------------------------------------
# TSA-6: idempotency — body already has ## Token Usage Summary → no edit
# ---------------------------------------------------------------------------


def test_tsa6_idempotency_skips_if_summary_present(tmp_path: Path) -> None:
    """PR body already contains ## Token Usage Summary → gh pr edit NOT called."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa6"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 10.0,
            },
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "## Token Usage Summary\n\n| Step | input |...\n"

    edit_calls: list = []

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "api" in args and "--method" in args:
            edit_calls.append(args)
        return view_result

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 0


# ---------------------------------------------------------------------------
# TSA-7: step name canonicalization
# ---------------------------------------------------------------------------


def test_tsa7_canonical_step_name() -> None:
    """_canonical strips trailing -N suffix; non-digit suffixes are preserved."""
    from autoskillit.hooks.token_summary_hook import _canonical

    assert _canonical("plan-30") == "plan"
    assert _canonical("open-pr-2") == "open-pr"
    assert _canonical("open-pr") == "open-pr"
    assert _canonical("plan-1") == "plan"
    assert _canonical("") == ""
    assert _canonical("implement") == "implement"


# ---------------------------------------------------------------------------
# TSA-8: gh pr edit failure → exits non-zero
# ---------------------------------------------------------------------------


def test_tsa8_gh_pr_edit_failure_exits_nonzero(tmp_path: Path) -> None:
    """gh pr edit returning non-zero → hook exits 0 (fail-open, error logged to stderr)."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "test-pipeline-tsa8"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "session-1",
                "cwd": "/some/worktree",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            },
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_result = MagicMock()
    view_result.returncode = 0
    view_result.stdout = "Existing body without summary."

    def subprocess_side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            raise subprocess.CalledProcessError(1, args)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=subprocess_side_effect):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0


# ---------------------------------------------------------------------------
# TSA-9: kitchen_id match + CWD mismatch → sessions FOUND → hook appends table
# ---------------------------------------------------------------------------


def test_tsa_kitchen_id_match_despite_cwd_mismatch(tmp_path: Path) -> None:
    """kitchen_id match + CWD mismatch → sessions FOUND → hook appends table.

    Production failure mode: hook fires in orchestrator dir, sessions have worktree CWD.
    kitchen_id correlation makes CWD irrelevant.
    """
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "test-kitchen-abc123"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/worktrees/impl-fix",
                "kitchen_id": kitchen_id,
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 20.0,
            }
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))
    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")
    view_result = MagicMock(returncode=0, stdout="Existing PR body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(
            event,
            log_root=log_root,
            hook_config_path=hook_config,
        )
    assert exit_code == 0
    assert len(edit_calls) == 1, "gh api PATCH must be called when kitchen_id matches"


# ---------------------------------------------------------------------------
# TSA-10: pipeline_id mismatch → no sessions found → exits 0, no gh pr edit
# ---------------------------------------------------------------------------


def test_tsa_kitchen_id_mismatch_exits_zero(tmp_path: Path) -> None:
    """Wrong kitchen_id → no sessions found → exits 0, no gh pr edit."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/worktree",
                "kitchen_id": "kitchen-A",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 10.0,
            }
        ],
    )
    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": "kitchen-B"}))
    event = _make_run_skill_event("pr_url=https://github.com/owner/repo/pull/99\n%%ORDER_UP%%")
    _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)
    assert exit_code == 0


def test_tsa_gh_pr_edit_stderr_captured(tmp_path: Path) -> None:
    """gh pr edit failure includes stderr in the logged error message."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "pipe-edit-test"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/1"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_ok = MagicMock(returncode=0, stdout="Some body.")
    error = subprocess.CalledProcessError(1, ["gh", "api", "repos/owner/repo/pulls/1"])
    error.stderr = "authentication required"  # only populated with capture_output=True

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_ok
        if "api" in args and "--method" in args:
            raise error
        return MagicMock(returncode=0)

    stderr_output: list[str] = []
    with patch("subprocess.run", side_effect=run_side):
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.write = lambda s: stderr_output.append(s)
            _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    combined = "".join(stderr_output)
    assert "authentication required" in combined, (
        "stderr from CalledProcessError must appear in diagnostic output — "
        "requires capture_output=True on the subprocess.run call"
    )


def test_tsa_gh_pr_view_failure_emits_diagnostic(tmp_path: Path) -> None:
    """gh pr view non-zero exit emits a stderr message before exiting 0."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    pipeline_id = "pipe-view-fail"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "s1",
                "cwd": "/w",
                "kitchen_id": pipeline_id,
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        ],
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": pipeline_id}))

    pr_url = "https://github.com/owner/repo/pull/2"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    view_fail = MagicMock(returncode=1, stderr="HTTP 401 Unauthorized", stdout="")

    stderr_output: list[str] = []
    with patch("subprocess.run", return_value=view_fail):
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.write = lambda s: stderr_output.append(s)
            _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    combined = "".join(stderr_output)
    assert combined.strip(), (
        "gh pr view failure must emit a diagnostic to stderr before exiting — "
        "silent sys.exit(0) makes auth/network errors indistinguishable from no-op"
    )


def test_tsa_humanize_preserves_decimal() -> None:
    """_humanize must use str(n) not str(int(n)) for sub-1000 values.

    Fails before P6-2 fix (str(int(45.7)) → '45'), passes after (str(45.7) → '45.7').
    Token counts are always integers in practice, so this is a semantic correctness fix.
    """
    from autoskillit.hooks.token_summary_hook import _humanize

    assert _humanize(999) == "999"
    assert _humanize(0) == "0"
    assert _humanize(None) == "0"
    # The key assertion: a float below 1000 must NOT be truncated to int
    assert _humanize(45.7) == "45.7", (
        "str(int(n)) truncates decimals — must be str(n) to match telemetry_fmt.py"
    )


# ---------------------------------------------------------------------------
# E-1 through E-4: order_id isolation in token_summary_appender
# ---------------------------------------------------------------------------


def _make_run_skill_event_with_order_id(
    result_text: str = "Done.\n%%ORDER_UP%%", order_id: str = ""
) -> dict:
    """Create a double-wrapped PostToolUse event for run_skill with optional order_id."""
    inner: dict = {"result": result_text, "success": True}
    if order_id:
        inner["order_id"] = order_id
    outer = {"result": json.dumps(inner)}
    return {
        "tool_name": "mcp__autoskillit_server__run_skill",
        "tool_response": json.dumps(outer),
    }


def test_e1_order_id_isolation_multi_issue_session(tmp_path: Path) -> None:
    """E-1: Three issues sharing kitchen_id; hook with order_id='B' includes only B's sessions."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "shared-kitchen"

    (log_root / "sessions.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "dir_name": "sess-A",
                    "cwd": "/w",
                    "kitchen_id": kitchen_id,
                    "order_id": "issue-A",
                    "step_name": "plan",
                },
                {
                    "dir_name": "sess-B",
                    "cwd": "/w",
                    "kitchen_id": kitchen_id,
                    "order_id": "issue-B",
                    "step_name": "implement",
                },
                {
                    "dir_name": "sess-C",
                    "cwd": "/w",
                    "kitchen_id": kitchen_id,
                    "order_id": "issue-C",
                    "step_name": "review",
                },
            ]
        )
        + "\n"
    )
    for dir_name, step in [("sess-A", "plan"), ("sess-B", "implement"), ("sess-C", "review")]:
        d = log_root / "sessions" / dir_name
        d.mkdir(parents=True)
        (d / "token_usage.json").write_text(
            json.dumps(
                {
                    "step_name": step,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "timing_seconds": 5.0,
                    "order_id": dir_name.replace("sess-", "issue-"),
                }
            )
        )

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event_with_order_id(
        f"pr_url={pr_url}\n%%ORDER_UP%%", order_id="issue-B"
    )
    view_result = MagicMock(returncode=0, stdout="Existing PR body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1, "gh api PATCH must be called once"
    body_arg = edit_calls[0][edit_calls[0].index("--raw-field") + 1]
    body_content = body_arg[len("body=") :]
    assert "implement" in body_content, "issue-B's step 'implement' must be in table"
    assert "plan" not in body_content, "issue-A's step 'plan' must NOT be in table"
    assert "review" not in body_content, "issue-C's step 'review' must NOT be in table"


def test_e2_fallback_to_kitchen_id_when_no_order_id(tmp_path: Path) -> None:
    """E-2: When run_skill result lacks 'order_id', hook falls back to kitchen_id filtering."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "my-kitchen"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "sess-1",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        ],
    )

    pr_url = "https://github.com/owner/repo/pull/10"
    # No order_id in the event
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))

    view_result = MagicMock(returncode=0, stdout="Existing body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1, "gh api PATCH must be called when kitchen_id matches (fallback)"


def test_e3_backward_compat_sessions_without_order_id_field(tmp_path: Path) -> None:
    """E-3: Sessions without 'order_id' key are skipped when order_id filter is active."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "kitchen-xyz"

    # Old session without order_id field
    (log_root / "sessions.jsonl").write_text(
        json.dumps(
            {
                "dir_name": "old-sess",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                # No order_id field
                "step_name": "plan",
            }
        )
        + "\n"
    )
    d = log_root / "sessions" / "old-sess"
    d.mkdir(parents=True)
    (d / "token_usage.json").write_text(
        json.dumps(
            {
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        )
    )

    pr_url = "https://github.com/owner/repo/pull/20"
    # order_id in event → activates order_id filtering
    event = _make_run_skill_event_with_order_id(
        f"pr_url={pr_url}\n%%ORDER_UP%%", order_id="issue-X"
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))

    with patch("subprocess.run") as mock_run:
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    # No sessions match order_id="issue-X" → exits 0 without calling gh api
    assert exit_code == 0
    # No edit call should be made since old sessions don't match the order_id
    patch_calls = [
        call for call in mock_run.call_args_list if "--method" in (call[0][0] if call[0] else [])
    ]
    assert len(patch_calls) == 0, "Old sessions without order_id should be skipped"


# ---------------------------------------------------------------------------
# T4 — token_summary_appender is fail-open (exit 0 on errors)
# ---------------------------------------------------------------------------


def test_token_summary_hook_patch_failure_exits_zero(tmp_path: Path) -> None:
    """CalledProcessError on PATCH must exit 0, not 1 (fail-open hook)."""
    event = {
        "tool_name": "mcp__autoskillit_server__run_skill",
        "tool_response": json.dumps({"result": json.dumps({"success": True})}),
    }
    # Use a valid-enough PR URL to trigger the gh api PATCH path
    pr_event = {
        **event,
        "tool_response": json.dumps(
            {
                "result": json.dumps(
                    {
                        "success": True,
                        "pr_url": "https://github.com/owner/repo/pull/1",
                    }
                )
            }
        ),
    }

    original_run = subprocess.run

    def failing_run(cmd, **kwargs):
        if "PATCH" in (cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)):
            raise subprocess.CalledProcessError(1, cmd, stderr="API error")
        return original_run(cmd, **kwargs)

    with patch("autoskillit.hooks.token_summary_hook.subprocess.run", failing_run):
        _, exit_code = _run_hook(
            event=pr_event,
            log_root=tmp_path,
        )
    # hook must exit 0 even when gh api PATCH raises CalledProcessError
    assert exit_code == 0


def test_token_summary_hook_unexpected_error_exits_zero(monkeypatch: object) -> None:
    """Unhandled exception in outer except must exit 0 (fail-open)."""

    # Patch json.loads to raise to trigger the outer except path

    original_loads = json.loads

    call_count = [0]

    def bomb_loads(s: str) -> object:
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("injected failure")
        return original_loads(s)

    with patch("autoskillit.hooks.token_summary_hook.json.loads", bomb_loads):
        _, exit_code = _run_hook(event={"tool_name": "any", "tool_response": "{}"})

    assert exit_code == 0


def test_e4_kitchen_id_renamed_in_hook_config(tmp_path: Path) -> None:
    """E-4: _read_kitchen_id reads 'kitchen_id' key; falls back to 'pipeline_id' for old."""
    from autoskillit.hooks.token_summary_hook import _read_kitchen_id

    # New format
    cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"kitchen_id": "new-kitchen-uuid"}))

    result = _read_kitchen_id(base=tmp_path)
    assert result == "new-kitchen-uuid"

    # Old format fallback
    cfg_path.write_text(json.dumps({"pipeline_id": "legacy-pipeline-uuid"}))
    result = _read_kitchen_id(base=tmp_path)
    assert result == "legacy-pipeline-uuid"


# ---------------------------------------------------------------------------
# _unwrap_mcp_response unit tests
# ---------------------------------------------------------------------------


class TestUnwrapMcpResponse:
    """Unit tests for the shared double-unwrap helper."""

    def _call(self, tool_name: str, raw: str):
        from autoskillit.hooks.token_summary_hook import _unwrap_mcp_response

        return _unwrap_mcp_response(tool_name, raw)

    def test_invalid_json_returns_none(self):
        assert self._call("mcp__x__y", "not json") is None

    def test_json_array_returns_none(self):
        import json

        assert self._call("mcp__x__y", json.dumps([1, 2, 3])) is None

    def test_non_mcp_tool_returns_outer_dict(self):
        import json

        payload = {"result": "some text", "success": True}
        result = self._call("run_python", json.dumps(payload))
        assert result == payload

    def test_mcp_tool_double_wrapped_returns_inner(self):
        import json

        inner = {"result": "https://github.com/o/r/pull/1", "order_id": "abc"}
        outer = {"result": json.dumps(inner)}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == inner

    def test_mcp_tool_inner_parse_fails_returns_outer(self):
        import json

        outer = {"result": "not-json-inner"}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == outer

    def test_mcp_tool_inner_not_dict_returns_outer(self):
        import json

        outer = {"result": json.dumps([1, 2, 3])}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == outer

    def test_mcp_tool_extra_keys_skips_double_unwrap(self):
        """Outer dict with more than just 'result' key → not a double-wrapped response."""
        import json

        outer = {"result": json.dumps({"foo": "bar"}), "extra": "key"}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == outer


def test_hook_subprocess_calls_have_timeout() -> None:
    """All subprocess.run() calls in token_summary_hook.py must have timeout=."""
    import ast

    src = Path("src/autoskillit/hooks/token_summary_hook.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            kw_names = {kw.arg for kw in node.keywords}
            assert "timeout" in kw_names, (
                f"subprocess.run() at line {node.lineno} in token_summary_hook.py missing timeout="
            )
