"""Verify bounded retry, terminal fail-fast, and verified response-loss recovery.

Execute compose-pr Step 5 against a local branch and a gh-compatible PR store.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_step_sections,
)

pytestmark = [
    pytest.mark.layer("skills"),
    pytest.mark.medium,
    pytest.mark.usefixtures("isolated_git_config"),
]
requires_jq = pytest.mark.skipif(
    shutil.which("jq") is None, reason="fake gh evaluates --jq with jq"
)

SKILL_PATH = (
    Path(__file__).parent.parent.parent / "src/autoskillit/skills_extended/compose-pr/SKILL.md"
)
HISTORICAL_OID = "c2ce139c7143fbcce82ba4592cd0c72738e507f4"


def _step5_bash() -> str:
    sections = extract_step_sections(SKILL_PATH.read_text(encoding="utf-8"))
    step5 = sections.get("Step 5")
    assert step5, "compose-pr/SKILL.md is missing a Step 5 section"
    blocks = extract_bash_blocks(step5)
    assert len(blocks) == 1, f"Expected exactly 1 bash block in Step 5, found {len(blocks)}"
    return blocks[0]


_FAKE_GH_TEMPLATE = r"""
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_GH_CALL_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

if args[:2] == ["pr", "create"]:
    counter = Path(os.environ["FAKE_GH_ATTEMPT_FILE"])
    attempt = int(counter.read_text(encoding="utf-8")) + 1
    counter.write_text(str(attempt), encoding="utf-8")
    for field, stream in (("STDOUT", sys.stdout), ("STDERR", sys.stderr)):
        value = os.environ.get(f"FAKE_GH_CREATE_{attempt}_{field}", "")
        if value:
            print(value, file=stream)
    sys.exit(int(os.environ.get(f"FAKE_GH_CREATE_{attempt}_EXIT", "0")))


def option(*names: str, default: str | None = None) -> str | None:
    for name in names:
        if name in args:
            return args[args.index(name) + 1]
    return default


prs = json.loads(Path(os.environ["FAKE_GH_PRS"]).read_text(encoding="utf-8"))
if args[:2] == ["pr", "list"]:
    head, base = option("--head"), option("--base")
    state = option("--state", default="open")
    states = {"open": {"OPEN"}, "closed": {"CLOSED", "MERGED"}, "merged": {"MERGED"}}
    result = [pr for pr in prs if
              (not head or pr["headRefName"] == head)
              and (not base or pr["baseRefName"] == base)
              and (state == "all" or pr["state"] in states[state])]
else:
    sys.exit(99)

fields = option("--json")
if fields:
    keys = fields.split(",")
    result = [{key: pr[key] for key in keys} for pr in result]
query = option("--jq", "-q")
if query:
    sys.exit(subprocess.run(["jq", "-r", query], input=json.dumps(result), text=True).returncode)
print(json.dumps(result))
"""


def _make_fake_gh(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n" + _FAKE_GH_TEMPLATE, encoding="utf-8")
    gh.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$FAKE_SLEEP_LOG"\n', encoding="utf-8")
    sleep.chmod(0o755)
    return bin_dir


def _git_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") or key in {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}
    }


def _work_repo(tmp_path: Path, branch: str = "test-branch") -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _git_env()
    for args in (
        ["init", "-q", "-b", branch],
        [
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "seed",
        ],
    ):
        subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, check=True)
    oid = subprocess.check_output(
        ["git", "rev-parse", f"refs/heads/{branch}"], cwd=repo, env=env, text=True
    ).strip()
    return repo, oid


def _pr(
    n: int,
    oid: str,
    *,
    state: str = "OPEN",
    head: str = "test-branch",
    base: str = "main",
    cross: bool = False,
) -> dict[str, str | bool]:
    return {
        "url": f"https://github.com/owner/repo/pull/{n}",
        "state": state,
        "headRefName": head,
        "headRefOid": oid,
        "baseRefName": base,
        "isCrossRepository": cross,
    }


def _history(branch: str = "test-branch") -> list[dict[str, str | bool]]:
    return [_pr(n, HISTORICAL_OID, state="CLOSED", head=branch) for n in (4, 17, 18, 22)]


def _run_step5(
    tmp_path: Path,
    bin_dir: Path,
    env_overrides: dict[str, str],
    *,
    store: Sequence[dict[str, str | bool]] = (),
    feature_branch: str = "test-branch",
    base_branch: str = "main",
    work_dir: Path | str | None = None,
    task_title: str = "Test PR",
) -> tuple[subprocess.CompletedProcess[str], int, list[list[str]]]:
    if work_dir is None:
        work_dir, _ = _work_repo(tmp_path, feature_branch)
    autotemp = tmp_path / "autotemp"
    log_dir = autotemp / "compose-pr"
    log_dir.mkdir(parents=True)
    (log_dir / "pr_body_testts.md").write_text("Summary\n\nCloses #123\n", encoding="utf-8")
    counter, call_log, prs = (tmp_path / name for name in ("attempts", "calls.jsonl", "prs.json"))
    counter.write_text("0", encoding="utf-8")
    call_log.write_text("", encoding="utf-8")
    prs.write_text(json.dumps(store), encoding="utf-8")
    bash = (
        _step5_bash().replace("{{AUTOSKILLIT_TEMP}}", str(autotemp)).replace("{run_id}", "testts")
    )
    values = {
        "WORK_DIR": str(work_dir),
        "FEATURE_BRANCH": feature_branch,
        "BASE_BRANCH": base_branch,
        "TASK_TITLE": task_title,
    }
    harness = (
        "set -u\n"
        + "\n".join(f"export {key}={shlex.quote(value)}" for key, value in values.items())
        + "\n"
        + bash
    )
    env = _git_env()
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        GIT_CEILING_DIRECTORIES=str(tmp_path),
        FAKE_GH_ATTEMPT_FILE=str(counter),
        FAKE_GH_CALL_LOG=str(call_log),
        FAKE_GH_PRS=str(prs),
        FAKE_SLEEP_LOG=str(tmp_path / "sleeps"),
        **env_overrides,
    )
    proc = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=30
    )
    return (
        proc,
        int(counter.read_text(encoding="utf-8")),
        [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()],
    )


def _assert_failure(proc):
    assert proc.returncode != 0
    assert "pr_url =" not in proc.stdout
    assert "gh pr create failed after" in proc.stderr


def _assert_success(proc, number):
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [f"pr_url = https://github.com/owner/repo/pull/{number}"]


def _create_attempts(attempts: list[tuple[str, str]]) -> dict[str, str]:
    return {
        f"FAKE_GH_CREATE_{n}_{field}": value
        for n, (exit_code, stderr) in enumerate(attempts, 1)
        for field, value in (("EXIT", exit_code), ("STDERR", stderr))
    }


class TestStep5Retry:
    def test_direct_create_success(self, tmp_path):
        proc, attempts, calls = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_STDOUT": "https://github.com/owner/repo/pull/42",
            },
        )
        _assert_success(proc, 42)
        assert attempts == 1
        assert [call[:2] for call in calls] == [["pr", "create"]]

    @requires_jq
    def test_transient_first_attempt_then_success(self, tmp_path):
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 503 Service Unavailable",
                "FAKE_GH_CREATE_2_STDOUT": "https://github.com/owner/repo/pull/42",
            },
        )
        _assert_success(proc, 42)
        assert attempts == 2
        assert "HTTP 503" not in proc.stdout

    @requires_jq
    def test_http_429_retries_and_succeeds_on_attempt_2(self, tmp_path):
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 429 Too Many Requests",
                "FAKE_GH_CREATE_2_STDOUT": "https://github.com/owner/repo/pull/99",
            },
        )
        _assert_success(proc, 99)
        assert attempts == 2

    @requires_jq
    def test_secondary_rate_limit_retries_before_success(self, tmp_path):
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 403: secondary rate limit exceeded",
                "FAKE_GH_CREATE_2_STDOUT": "https://github.com/owner/repo/pull/100",
            },
        )
        _assert_success(proc, 100)
        assert attempts == 2

    @requires_jq
    def test_three_transient_failures_exhaust_retry_budget(self, tmp_path):
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            _create_attempts([("1", f"HTTP 503 attempt {n} unavailable") for n in range(1, 4)]),
        )
        _assert_failure(proc)
        assert attempts == 3
        assert "gh pr create failed after 3 attempt(s):" in proc.stderr
        assert "HTTP 503 attempt 3 unavailable" in proc.stderr
        assert (tmp_path / "sleeps").read_text(encoding="utf-8").splitlines() == ["1", "2"]
        assert "attempt(s): 4" not in proc.stderr
        assert "$PR_CREATE_ATTEMPT" not in proc.stderr

    @pytest.mark.parametrize("field", ["work_dir", "feature_branch", "base_branch", "task_title"])
    def test_empty_required_input_stops_before_create(self, tmp_path, field):
        inputs = {"work_dir": str(tmp_path), field: ""}
        proc, attempts, calls = _run_step5(tmp_path, _make_fake_gh(tmp_path), {}, **inputs)
        assert proc.returncode != 0
        assert "pr_url =" not in proc.stdout
        assert "Step 5 could not run:" in proc.stderr
        assert "must all be non-empty." in proc.stderr
        assert attempts == 0
        assert calls == []

    @pytest.mark.parametrize("branch", ["test-branch", "fix-timeout-handling/7"])
    def test_no_commits_rejection_is_terminal(self, tmp_path, branch):
        repo, _ = _work_repo(tmp_path, branch)
        diagnostic = (
            "pull request create failed: GraphQL: No commits between main and "
            f"{branch} (createPullRequest)"
        )
        proc, attempts, calls = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": diagnostic,
            },
            store=_history(branch),
            feature_branch=branch,
            work_dir=repo,
        )
        _assert_failure(proc)
        assert attempts == 1
        assert diagnostic in proc.stderr
        assert [call[:2] for call in calls] == [["pr", "create"]]

    @requires_jq
    @pytest.mark.parametrize(
        "case",
        [
            "closed_at_head",
            "merged_at_head",
            "open_other_commit",
            "open_other_base",
            "open_fork",
            "two_open_matching",
            "zero_exit_without_url",
            "unresolvable_head",
        ],
    )
    def test_recovery_rejects_unverifiable_candidate(self, tmp_path, case):
        repo, oid = _work_repo(tmp_path)
        overrides = {
            "closed_at_head": {"state": "CLOSED"},
            "merged_at_head": {"state": "MERGED"},
            "open_other_commit": {"oid": HISTORICAL_OID},
            "open_other_base": {"base": "develop"},
            "open_fork": {"cross": True},
            "zero_exit_without_url": {"state": "CLOSED"},
        }.get(case, {})
        store = [_pr(5, **{"oid": oid, **overrides})]
        if case == "two_open_matching":
            store.append(_pr(6, oid))
        if case == "unresolvable_head":
            repo = tmp_path / "not-a-repo"
            repo.mkdir()
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            _create_attempts(
                [
                    (
                        "0" if case == "zero_exit_without_url" else "1",
                        "" if case == "zero_exit_without_url" else "HTTP 502 Bad Gateway",
                    )
                ]
                * 3
            ),
            store=store,
            work_dir=repo,
        )
        _assert_failure(proc)
        assert attempts == 3

        if case == "unresolvable_head":
            assert "Verified PR recovery is disabled:" in proc.stderr
            assert "could not resolve the local feature-branch head." in proc.stderr

    def test_terminal_validation_error_does_not_retry(self, tmp_path):
        repo, oid = _work_repo(tmp_path)
        proc, attempts, calls = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 422 validation failed: missing required field",
            },
            store=[_pr(5, oid)],
            work_dir=repo,
        )
        _assert_failure(proc)
        assert attempts == 1
        assert "validation failed" in proc.stderr
        assert [call[:2] for call in calls] == [["pr", "create"]]

    @requires_jq
    def test_lost_response_recovers_single_verified_open_pr(self, tmp_path):
        repo, oid = _work_repo(tmp_path)
        proc, attempts, calls = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 502 Bad Gateway",
            },
            store=[*_history(), _pr(23, oid)],
            work_dir=repo,
        )
        _assert_success(proc, 23)
        assert attempts == 1
        assert "Bad Gateway" not in proc.stdout
        assert [call[:2] for call in calls] == [["pr", "create"], ["pr", "list"]]
        lookup = calls[1]
        for key, value in (("--head", "test-branch"), ("--base", "main"), ("--state", "open")):
            assert lookup[lookup.index(key) + 1] == value

    @requires_jq
    @pytest.mark.parametrize("branch", ["test-branch", "add-input-validation/12"])
    def test_duplicate_pr_signal_recovers_verified_pr(self, tmp_path, branch):
        repo, oid = _work_repo(tmp_path, branch)
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": (
                    f'a pull request for branch "{branch}" into branch "main" already exists:\n'
                    "https://github.com/owner/repo/pull/5"
                ),
            },
            store=[_pr(5, oid, head=branch)],
            feature_branch=branch,
            work_dir=repo,
        )
        _assert_success(proc, 5)
        assert attempts == 1

    @requires_jq
    @pytest.mark.parametrize("create_exit", ["0", "1"])
    def test_empty_stderr_recovers_verified_pr(self, tmp_path, create_exit):
        repo, oid = _work_repo(tmp_path)
        proc, attempts, calls = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": create_exit,
            },
            store=[_pr(31, oid)],
            work_dir=repo,
        )
        _assert_success(proc, 31)
        assert attempts == 1
        assert [call[:2] for call in calls] == [["pr", "create"], ["pr", "list"]]

    @requires_jq
    def test_fork_candidate_is_filtered_before_counting(self, tmp_path):
        repo, oid = _work_repo(tmp_path)
        proc, attempts, _ = _run_step5(
            tmp_path,
            _make_fake_gh(tmp_path),
            {
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 502 Bad Gateway",
            },
            store=[_pr(40, oid), _pr(41, oid, cross=True)],
            work_dir=repo,
        )
        _assert_success(proc, 40)
        assert attempts == 1
